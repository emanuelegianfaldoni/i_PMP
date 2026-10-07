"""
Inizializza il database SQLite per i test locali (senza HANA Cloud).
Uso:
  python3 init_sqlite.py
"""
import sqlite3, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

db_path = os.getenv("SQLITE_PATH", "ipmp_test.db")
schema = Path("sql/schema_sqlite.sql").read_text()

conn = sqlite3.connect(db_path)
for stmt in schema.split(";"):
    s = stmt.strip()
    if s:
        conn.execute(s)
conn.commit()
conn.close()

print(f"✅ Database SQLite creato: {db_path}")
print("   Tabelle: IPMP_DOCS, IPMP_DECISIONS, IPMP_CONTEXT, IPMP_ALERTS")
