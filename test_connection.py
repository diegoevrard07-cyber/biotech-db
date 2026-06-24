import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("❌ DATABASE_URL not found in .env")
    exit(1)

print(f"Connecting to: {db_url.split('@')[1]}")

try:
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT version();")
    version = cur.fetchone()[0]
    print(f"✅ SUCCESS")
    print(f"Postgres version: {version}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"❌ FAILED: {e}")
