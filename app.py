from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_sock import Sock
from config import Config
from stratum_proxy import create_session
import os
import time
import sys
import json
import logging
import mimetypes
from sqlalchemy import text, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ProgrammingError, OperationalError
import os

# Ensure DB sessions run with the project schema (default to 'minewithme')
PROJECT_SCHEMA = os.getenv('PROJECT_SCHEMA') or 'minewithme'
@event.listens_for(Engine, "connect")
def _set_search_path(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET search_path TO {PROJECT_SCHEMA};")
        cursor.close()
    except Exception:
        pass

# Ensure .wasm files are served with correct MIME type
mimetypes.add_type('application/wasm', '.wasm')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
sock = Sock(app)

class Stats(db.Model):
    __table_args__ = {'schema': PROJECT_SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    total_hashrate = db.Column(db.Float, default=0.0)   # MH/s
    total_shares = db.Column(db.Integer, default=0)
    estimated_xmr = db.Column(db.Float, default=0.0)   # net (after dev fee)
    gross_estimated_xmr = db.Column(db.Float, default=0.0)  # gross estimated XMR
    dev_fee_collected = db.Column(db.Float, default=0.0)    # collected dev fee in XMR

# Ensure table schema is applied (guard for empty env values)
try:
    if not PROJECT_SCHEMA:
        PROJECT_SCHEMA = 'minewithme'
    Stats.__table__.schema = PROJECT_SCHEMA
    logger.info(f"Stats.__table__.schema set to: {Stats.__table__.schema}")
except Exception as e:
    logger.warning(f"Could not set Stats.__table__.schema at import: {e}")

@app.route('/')
def index():
    try:
        stats = Stats.query.first()
    except (ProgrammingError, OperationalError) as e:
        logger.warning('DB query failed in index: %s — attempting to ensure columns and retry', e)
        try:
            ensure_columns()
            stats = Stats.query.first()
        except Exception as e2:
            logger.error('Retry after ensure_columns failed: %s', e2)
            return "Database not ready", 503

    if not stats:
        stats = Stats(total_hashrate=0, total_shares=0, estimated_xmr=0)
        db.session.add(stats)
        db.session.commit()
    return render_template('index.html', 
                         stats=stats, 
                         xmr_wallet=app.config['XMR_WALLET'],
                         pool_url=app.config['POOL_URL'])

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats = Stats.query.first()
    return jsonify({
        'total_hashrate': stats.total_hashrate,
        'total_shares': stats.total_shares,
        'estimated_xmr': stats.estimated_xmr,
        'gross_estimated_xmr': stats.gross_estimated_xmr,
        'dev_fee_collected': stats.dev_fee_collected
    })


@app.route('/healthz', methods=['GET'])
def healthz():
    """Light health check with debug info: verifies DB connectivity and reports search_path and table presence."""
    try:
        # lightweight DB touch
        db.session.execute(text('SELECT 1'))
        # report current search_path and check for stats table
        schema_row = db.session.execute(text("SELECT current_schema() as cs, current_setting('search_path') as sp")).mappings().first()
        tables = db.session.execute(text("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name='stats' ORDER BY table_schema")).fetchall()
        tables_info = [{'schema': r[0], 'name': r[1]} for r in tables]
        return jsonify({'status': 'ok', 'current_schema': schema_row['cs'], 'search_path': schema_row['sp'], 'tables': tables_info}), 200
    except Exception as e:
        logger.warning('Health check failed: %s', e)
        # try to return some DB error details
        try:
            row = db.session.execute(text("SELECT current_setting('search_path')")).scalar()
        except Exception:
            row = None
        return jsonify({'status': 'error', 'details': str(e), 'search_path': row}), 503

@app.route('/api/submit', methods=['POST'])
def submit_stats():
    data = request.json
    stats = Stats.query.first()
    if stats:
        # hashrate from client is H/s, store in MH/s for global stat
        stats.total_hashrate = data.get('hashrate', 0) / 1000   # в MH/s
        stats.total_shares += data.get('shares', 0)

        # Client should send estimated gross XMR (e.g., estimated XMR/day)
        gross = float(data.get('estimated', 0.0))
        dev_fee = gross * Config.DEV_FEE
        net = gross - dev_fee

        stats.gross_estimated_xmr += gross
        stats.dev_fee_collected += dev_fee
        stats.estimated_xmr += net

        db.session.commit()
    return jsonify({'status': 'ok'})

def ensure_columns():
    engine = db.get_engine()
    schema = PROJECT_SCHEMA or 'public'
    table_name = f"{schema}.stats"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS gross_estimated_xmr FLOAT DEFAULT 0"))
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS dev_fee_collected FLOAT DEFAULT 0"))


@sock.route('/ws/mining')
def mining_ws(ws):
    """WebSocket endpoint — per-session pool connection with wallet switching."""
    logger.info("New WebSocket mining connection")
    
    dev_wallet = app.config['XMR_WALLET']
    pool_url = app.config['POOL_URL']
    
    # Create per-session proxy (starts with dev wallet, switches when user sets wallet)
    session = create_session(pool_url, dev_wallet)
    if not session:
        try:
            ws.send(json.dumps({"type": "error", "message": "Cannot connect to mining pool"}))
        except Exception:
            pass
        return

    def on_pool_msg(msg_str):
        """Forward pool messages to this browser client."""
        try:
            ws.send(msg_str)
        except Exception:
            pass

    session.set_listener(on_pool_msg)
    logger.info(f"Browser miner connected, session has job: {session.job is not None}")

    try:
        while True:
            try:
                data = ws.receive(timeout=60)
            except Exception as recv_err:
                logger.info(f"WebSocket receive error: {recv_err}")
                break
            if data is None:
                break
            try:
                msg = json.loads(data)
                msg_type = msg.get('type', '')

                if msg_type == 'set_wallet':
                    # Browser sends user's XMR wallet for 85% rewards
                    user_wallet = msg.get('wallet', '')
                    session.set_user_wallet(user_wallet)
                    ws.send(json.dumps({
                        "type": "wallet_ack",
                        "has_user_wallet": session.has_user_wallet,
                        "message": f"Wallet set: 85% user / 15% dev" if session.has_user_wallet else "No wallet: 100% dev"
                    }))

                elif msg_type == 'submit':
                    # Browser found a valid share
                    nonce = msg.get('nonce', '')
                    result_hash = msg.get('result', '')
                    job_id = msg.get('job_id', '')
                    success = session.submit_share(nonce, result_hash, job_id)
                    ws.send(json.dumps({"type": "submit_ack", "success": success}))
                    logger.info(f"Share submitted: nonce={nonce[:8]}... job={job_id} success={success}")

                elif msg_type == 'get_job':
                    # Browser requests current job
                    if session.job:
                        job_msg = json.dumps({"method": "job", "params": session.job})
                        ws.send(job_msg)
                        logger.info(f"Sent cached job to browser: {session.job.get('job_id', '?')}")
                    else:
                        logger.warning("Browser requested job but session has no job yet")
                        time.sleep(2)
                        if session.job:
                            ws.send(json.dumps({"method": "job", "params": session.job}))

                elif msg_type == 'keepalive':
                    ws.send(json.dumps({"type": "keepalive_ack"}))

            except json.JSONDecodeError:
                logger.warning("Invalid JSON from browser")
    except Exception as e:
        logger.info(f"Browser miner disconnected: {e}")
    finally:
        session.disconnect()
        logger.info("Browser session closed, pool connection terminated")


def init_db_with_retry(max_retries=5, delay=2):
    """Инициализация БД с повторными попытками"""
    for attempt in range(max_retries):
        try:
            with app.app_context():
                db.create_all()
                ensure_columns()
                print(f"✅ База данных успешно инициализирована (и колонки проверены)")
                return True
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⏳ Попытка {attempt + 1}/{max_retries}: База не готова, ждём {delay}с...")
                time.sleep(delay)
            else:
                print(f"❌ Не удалось подключиться к базе после {max_retries} попыток")
                print(f"Ошибка: {e}")
                sys.exit(1)
    return False

if __name__ == '__main__':
    init_db_with_retry()
    app.run(host='0.0.0.0', port=5000)
