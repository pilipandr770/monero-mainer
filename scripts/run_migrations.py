#!/usr/bin/env python3
"""Простой скрипт для применения SQL миграций к DATABASE_URL из окружения.
Используйте его для CI или ручного запуска: `python scripts/run_migrations.py`.
"""
import os
import sys
from urllib.parse import urlparse

import psycopg2

HERE = os.path.dirname(__file__)
SQL_FILE = os.path.join(HERE, '..', 'init.sql')


def run():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL not set in environment', file=sys.stderr)
        sys.exit(1)

    with open(SQL_FILE, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Create schema if provided as env SCHEMA or default to "minewithme"
    schema = os.getenv('PROJECT_SCHEMA', 'minewithme')
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
        cur.execute(f"SET search_path TO {schema};")
        cur.execute(sql)
    conn.close()
    print('Migrations applied to schema:', schema)


if __name__ == '__main__':
    run()
