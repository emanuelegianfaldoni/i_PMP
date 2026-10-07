import json
import sys
from services.ai_core import get_embedding
from services.hana import get_connection, is_sqlite


class IndexingAgent:
    def indicizza(self, chunks: list[str], source_name: str, project_id: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        indexed = 0
        try:
            for chunk in chunks:
                try:
                    emb = get_embedding(chunk)
                    emb_str = json.dumps(emb)
                    if is_sqlite():
                        cursor.execute(
                            "INSERT INTO IPMP_DOCS (PROJECT_ID, SOURCE_NAME, CONTENT, EMBEDDING) "
                            "VALUES (?, ?, ?, ?)",
                            [project_id, source_name, chunk, emb_str],
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO IPMP_DOCS (PROJECT_ID, SOURCE_NAME, CONTENT, EMBEDDING) "
                            "VALUES (?, ?, ?, TO_REAL_VECTOR(?))",
                            [project_id, source_name, chunk, emb_str],
                        )
                    indexed += 1
                except Exception as e:
                    print(f"[IndexingAgent] Errore su chunk: {e}", file=sys.stderr)
            conn.commit()
        finally:
            cursor.close()
            conn.close()
        return indexed
