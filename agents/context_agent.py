import json
import sys
from services.ai_core import chat_completion, get_embedding
from services.hana import get_connection, is_sqlite

_MAX_TEXT = 8000

_PROMPT = (
    "Genera un riassunto strutturato di questo documento in circa 300 parole, "
    "catturando il tema principale, i punti chiave, i partecipanti se presenti "
    "e le conclusioni. Rispondi in italiano."
)


class ContextAgent:
    def process(self, full_text: str, source_name: str, project_id: str) -> bool:
        text = full_text[:_MAX_TEXT] if len(full_text) > _MAX_TEXT else full_text
        try:
            summary = chat_completion(
                messages=[{"role": "user", "content": f"{_PROMPT}\n\n---\n{text}"}],
            )
            emb = get_embedding(summary)
            emb_str = json.dumps(emb)

            conn = get_connection()
            cursor = conn.cursor()
            try:
                if is_sqlite():
                    cursor.execute(
                        "INSERT INTO IPMP_CONTEXT (PROJECT_ID, SOURCE_NAME, SUMMARY, EMBEDDING) "
                        "VALUES (?, ?, ?, ?)",
                        [project_id, source_name, summary, emb_str],
                    )
                else:
                    cursor.execute(
                        "INSERT INTO IPMP_CONTEXT (PROJECT_ID, SOURCE_NAME, SUMMARY, EMBEDDING) "
                        "VALUES (?, ?, ?, TO_REAL_VECTOR(?))",
                        [project_id, source_name, summary, emb_str],
                    )
                conn.commit()
            finally:
                cursor.close()
                conn.close()
            return True
        except Exception as e:
            print(f"[ContextAgent] Errore: {e}", file=sys.stderr)
            return False
