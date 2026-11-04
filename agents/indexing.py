import json
import sys
from services.ai_core import get_embedding
from services.hana import get_connection, is_sqlite


class IndexingAgent:
    def indicizza(self, chunks: list[str], source_name: str, project_id: str,
                   content_hash: str = None, job=None) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        indexed = 0
        try:
            for chunk in chunks:
                if job and job.annullato:
                    break
                try:
                    emb = get_embedding(chunk)
                    emb_str = json.dumps(emb)
                    if is_sqlite():
                        cursor.execute(
                            "INSERT INTO IPMP_DOCS (PROJECT_ID, SOURCE_NAME, CONTENT, EMBEDDING, CONTENT_HASH) "
                            "VALUES (?, ?, ?, ?, ?)",
                            [project_id, source_name, chunk, emb_str, content_hash],
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO IPMP_DOCS (PROJECT_ID, SOURCE_NAME, CONTENT, EMBEDDING, CONTENT_HASH) "
                            "VALUES (?, ?, ?, TO_REAL_VECTOR(?), ?)",
                            [project_id, source_name, chunk, emb_str, content_hash],
                        )
                    indexed += 1
                except Exception as e:
                    print(f"[IndexingAgent] Errore su chunk: {e}", file=sys.stderr)
                if job:
                    job.avanza(indicizzati=1)
            conn.commit()
        finally:
            cursor.close()
            conn.close()
        return indexed
