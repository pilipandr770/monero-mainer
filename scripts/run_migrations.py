#!/usr/bin/env python3
"""Простой скрипт для применения SQL миграций к DATABASE_URL из окружения.
Сценарий выполняет retries при недоступной БД и выводит понятные логи.
Используйте его для CI или авто-запуска при старте контейнера: `python scripts/run_migrations.py`.
"""
import os
import sys
import time
import logging

import psycopg2
from psycopg2 import OperationalError

HERE = os.path.dirname(__file__)
SQL_FILE = os.path.join(HERE, '..', 'init.sql')

logging.basicConfig(level=logging.INFO, format='[migrations] %(message)s')


def run():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        logging.error('DATABASE_URL not set in environment')
        sys.exit(1)

    schema = os.getenv('PROJECT_SCHEMA', 'minewithme')

    # Read SQL once
    with open(SQL_FILE, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Retry loop for DB availability
    attempts = 0
    max_attempts = int(os.getenv('MIGRATE_MAX_ATTEMPTS', '12'))  # default ~60s with 5s sleep
    sleep_seconds = int(os.getenv('MIGRATE_RETRY_SECONDS', '5'))

    while attempts < max_attempts:
        try:
            logging.info(f'Connecting to DB (attempt {attempts+1}/{max_attempts})')
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
                cur.execute(f"SET search_path TO {schema};")
                cur.execute(sql)
            conn.close()
            logging.info('Migrations applied to schema: %s', schema)
            return
        except OperationalError as e:
            logging.warning('DB not ready: %s', e)
            attempts += 1
            time.sleep(sleep_seconds)
        except Exception as e:
            logging.error('Migration failed: %s', e)
            sys.exit(1)

    logging.error('Could not connect to DB after %s attempts', max_attempts)
    sys.exit(1)


if __name__ == '__main__':
    run()
