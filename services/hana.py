import os
import math


def migrate(conn) -> None:
    """Applica migrazioni schema incrementali senza perdere dati."""
    cursor = conn.cursor()
    try:
        # IPMP_DECISIONS: aggiungi EMBEDDING se mancante
        try:
            cursor.execute("ALTER TABLE IPMP_DECISIONS ADD COLUMN EMBEDDING TEXT")
            conn.commit()
        except Exception:
            pass  # colonna già presente
    finally:
        cursor.close()


def is_sqlite() -> bool:
    return os.getenv("USE_SQLITE", "").lower() == "true"


def get_connection():
    if is_sqlite():
        import sqlite3
        db_path = os.getenv("SQLITE_PATH", "ipmp_test.db")
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn
    from hdbcli import dbapi
    return dbapi.connect(
        address=os.getenv("HANA_HOST"),
        port=int(os.getenv("HANA_PORT", 443)),
        user=os.getenv("HANA_USER"),
        password=os.getenv("HANA_PASSWORD"),
        encrypt=True,
        sslValidateCertificate=False,
    )


def cosine_similarity(v1: list, v2: list) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)
