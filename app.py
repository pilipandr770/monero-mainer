from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from config import Config
import os
import time
import sys
from sqlalchemy import text

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

class Stats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    total_hashrate = db.Column(db.Float, default=0.0)   # MH/s
    total_shares = db.Column(db.Integer, default=0)
    estimated_xmr = db.Column(db.Float, default=0.0)   # net (after dev fee)
    gross_estimated_xmr = db.Column(db.Float, default=0.0)  # gross estimated XMR
    dev_fee_collected = db.Column(db.Float, default=0.0)    # collected dev fee in XMR

@app.route('/')
def index():
    stats = Stats.query.first()
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
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE stats
            ADD COLUMN IF NOT EXISTS gross_estimated_xmr FLOAT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS dev_fee_collected FLOAT DEFAULT 0
        """))


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
