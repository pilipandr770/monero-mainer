#!/usr/bin/env python3
"""Inspect columns for minewithme.stats using DATABASE_URL from .env"""
from dotenv import load_dotenv
load_dotenv()
import os
import psycopg2

db = os.getenv('DATABASE_URL')
if not db:
    raise SystemExit('DATABASE_URL not set')

conn = psycopg2.connect(db)
cur = conn.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='minewithme' AND table_name='stats' ORDER BY ordinal_position;")
rows = cur.fetchall()
print('columns:', rows)
cur.close()
conn.close()