from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from config import Config
import os
import time
import sys

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

class Stats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    total_hashrate = db.Column(db.Float, default=0.0)   # MH/s
    total_shares = db.Column(db.Integer, default=0)
    estimated_xmr = db.Column(db.Float, default=0.0)

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
        'estimated_xmr': stats.estimated_xmr
    })

@app.route('/api/submit', methods=['POST'])
def submit_stats():
    data = request.json
    stats = Stats.query.first()
    if stats:
        stats.total_hashrate = data.get('hashrate', 0) / 1000   # в MH/s
        stats.total_shares += data.get('shares', 0)
        stats.estimated_xmr += data.get('estimated', 0) * Config.DEV_FEE
        db.session.commit()
    return jsonify({'status': 'ok'})

def init_db_with_retry(max_retries=5, delay=2):
    """Инициализация БД с повторными попытками"""
    for attempt in range(max_retries):
        try:
            with app.app_context():
                db.create_all()
                print(f"✅ База данных успешно инициализирована")
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
