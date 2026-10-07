import json
import sys
from datetime import date
from services.ai_core import chat_completion, get_embedding
from services.hana import get_connection, is_sqlite

_BATCH = 8

_SYSTEM = (
    "Sei un analista di progetto. Rispondi ESCLUSIVAMENTE con un array JSON valido, "
    "senza markdown, senza spiegazioni."
)

_TEMPLATE = (
    "Analizza i seguenti testi numerati. Per ogni testo che contiene una decisione formale "
    "del progetto, includi un oggetto nell'array con: "
    '{"indice": N, "descrizione": "...", "autore": "...", "data": "YYYY-MM-DD o null", "motivazione": "..."}.\n'
    "Se nessun testo contiene decisioni, rispondi con []. Non includere testi senza decisioni.\n\n"
    "{testi}"
)


class DecisionTrackerAgent:
    def process(self, chunks: list[str], source_name: str, project_id: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        found = 0
        try:
            for i in range(0, len(chunks), _BATCH):
                batch = chunks[i:i + _BATCH]
                testi = "\n\n".join(
                    f"[{j+1}] {c[:800]}" for j, c in enumerate(batch)
                )
                try:
                    raw = chat_completion(
                        messages=[{"role": "user", "content": _TEMPLATE.format(testi=testi)}],
                        system=_SYSTEM,
                        temperature=0.0,
                    )
                    decisioni = json.loads(raw.strip())
                    if not isinstance(decisioni, list):
                        continue
                    for d in decisioni:
                        data_decisione = d.get("data")
                        if data_decisione:
                            try:
                                data_decisione = date.fromisoformat(data_decisione)
                            except (ValueError, TypeError):
                                data_decisione = None
                        descrizione = d.get("descrizione", "")
                        try:
                            emb = get_embedding(descrizione)
                            emb_str = json.dumps(emb)
                        except Exception:
                            emb_str = None

                        if is_sqlite():
                            cursor.execute(
                                "INSERT INTO IPMP_DECISIONS "
                                "(PROJECT_ID, DESCRIZIONE, AUTORE, DATA_DECISIONE, MOTIVAZIONE, SOURCE_NAME, EMBEDDING) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                [project_id, descrizione, d.get("autore", ""),
                                 data_decisione, d.get("motivazione", ""), source_name, emb_str],
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO IPMP_DECISIONS "
                                "(PROJECT_ID, DESCRIZIONE, AUTORE, DATA_DECISIONE, MOTIVAZIONE, SOURCE_NAME, EMBEDDING) "
                                "VALUES (?, ?, ?, ?, ?, ?, TO_REAL_VECTOR(?))",
                                [project_id, descrizione, d.get("autore", ""),
                                 data_decisione, d.get("motivazione", ""), source_name, emb_str],
                            )
                        found += 1
                except (json.JSONDecodeError, Exception) as e:
                    print(f"[DecisionTracker] Batch saltato: {e}", file=sys.stderr)

            conn.commit()
        finally:
            cursor.close()
            conn.close()
        return found
