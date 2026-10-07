import json
from services.ai_core import get_embedding
from services.hana import get_connection, is_sqlite, cosine_similarity


class RetrievalAgent:
    def cerca(self, query: str, project_id: str, top_k: int = 5) -> list[dict]:
        emb = get_embedding(query)
        emb_str = json.dumps(emb)

        conn = get_connection()
        cursor = conn.cursor()
        results = []
        try:
            if is_sqlite():
                cursor.execute(
                    "SELECT ID, SOURCE_NAME, CONTENT, CREATED_AT, EMBEDDING FROM IPMP_DOCS "
                    "WHERE PROJECT_ID=? ",
                    [project_id],
                )
                rows = cursor.fetchall()
                scored = []
                for row in rows:
                    try:
                        doc_vec = json.loads(row[4])
                        score = cosine_similarity(emb, doc_vec)
                        scored.append((row[0], row[1], row[2], row[3], score))
                    except Exception:
                        pass
                scored.sort(key=lambda x: x[4], reverse=True)
                for r in scored[:top_k]:
                    results.append({"id": r[0], "source": r[1], "content": r[2],
                                    "created_at": str(r[3]), "score": r[4], "type": "doc"})

                cursor.execute(
                    "SELECT ID, SOURCE_NAME, SUMMARY, CREATED_AT, EMBEDDING FROM IPMP_CONTEXT "
                    "WHERE PROJECT_ID=?", [project_id],
                )
                ctx_rows = cursor.fetchall()
                ctx_scored = []
                for row in ctx_rows:
                    try:
                        doc_vec = json.loads(row[4])
                        score = cosine_similarity(emb, doc_vec)
                        ctx_scored.append((row[0], row[1], row[2], row[3], score))
                    except Exception:
                        pass
                ctx_scored.sort(key=lambda x: x[4], reverse=True)
                for r in ctx_scored[:3]:
                    results.append({"id": r[0], "source": r[1], "content": r[2],
                                    "created_at": str(r[3]), "score": r[4], "type": "context"})

                cursor.execute(
                    "SELECT ID, SOURCE_NAME, DESCRIZIONE, DATA_DECISIONE, AUTORE "
                    "FROM IPMP_DECISIONS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC LIMIT 5",
                    [project_id],
                )
            else:
                cursor.execute(
                    f"SELECT TOP {top_k} ID, SOURCE_NAME, CONTENT, CREATED_AT, "
                    f"COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE "
                    f"FROM IPMP_DOCS "
                    f"WHERE PROJECT_ID=?  "
                    f"ORDER BY SCORE DESC",
                    [emb_str, project_id],
                )
                for row in cursor.fetchall():
                    results.append({"id": row[0], "source": row[1], "content": row[2],
                                    "created_at": str(row[3]), "score": float(row[4]), "type": "doc"})

                cursor.execute(
                    f"SELECT TOP 3 ID, SOURCE_NAME, SUMMARY, CREATED_AT, "
                    f"COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE "
                    f"FROM IPMP_CONTEXT WHERE PROJECT_ID=? ORDER BY SCORE DESC",
                    [emb_str, project_id],
                )
                for row in cursor.fetchall():
                    results.append({"id": row[0], "source": row[1], "content": row[2],
                                    "created_at": str(row[3]), "score": float(row[4]), "type": "context"})

                cursor.execute(
                    "SELECT TOP 5 ID, SOURCE_NAME, DESCRIZIONE, DATA_DECISIONE, AUTORE "
                    "FROM IPMP_DECISIONS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC",
                    [project_id],
                )

            for row in cursor.fetchall():
                desc = row[2] or ""
                results.append({"id": row[0], "source": row[1] or "Decision Log",
                                "content": f"Decisione: {desc} (Autore: {row[4]}, Data: {row[3]})",
                                "created_at": str(row[3]), "score": 0.5, "type": "decision"})
        finally:
            cursor.close()
            conn.close()

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k + 5]

    def cerca_contesto(self, query: str, project_id: str, top_k: int = 5) -> list[dict]:
        """Ricerca semantica su IPMP_CONTEXT (riassunti documento-livello) per domande trasversali."""
        emb = get_embedding(query)
        emb_str = json.dumps(emb)

        conn = get_connection()
        cursor = conn.cursor()
        results = []
        try:
            if is_sqlite():
                cursor.execute(
                    "SELECT ID, SOURCE_NAME, SUMMARY, CREATED_AT, EMBEDDING FROM IPMP_CONTEXT "
                    "WHERE PROJECT_ID=?",
                    [project_id],
                )
                rows = cursor.fetchall()
                scored = []
                for row in rows:
                    try:
                        doc_vec = json.loads(row[4])
                        score = cosine_similarity(emb, doc_vec)
                        scored.append((row[0], row[1], row[2], row[3], score))
                    except Exception:
                        pass
                scored.sort(key=lambda x: x[4], reverse=True)
                for r in scored[:top_k]:
                    results.append({"id": r[0], "source": r[1], "content": r[2],
                                    "created_at": str(r[3]), "score": r[4], "type": "context"})
            else:
                cursor.execute(
                    f"SELECT TOP {top_k} ID, SOURCE_NAME, SUMMARY, CREATED_AT, "
                    f"COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE "
                    f"FROM IPMP_CONTEXT WHERE PROJECT_ID=? ORDER BY SCORE DESC",
                    [emb_str, project_id],
                )
                for row in cursor.fetchall():
                    results.append({"id": row[0], "source": row[1], "content": row[2],
                                    "created_at": str(row[3]), "score": float(row[4]), "type": "context"})
        finally:
            cursor.close()
            conn.close()
        return results
