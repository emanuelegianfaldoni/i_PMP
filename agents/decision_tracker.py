import json
import sys
from datetime import date
from services.ai_core import chat_completion, get_embedding
from services.hana import get_connection, is_sqlite, cosine_similarity

_BATCH = 8
# Due decisioni dello stesso documento più simili di così sono la stessa decisione citata due volte
# (tarata sui dati reali: doppione 0.845, decisioni diverse nello stesso documento fino a 0.753)
_SOGLIA_DOPPIONE = 0.83

_SYSTEM = (
    "Sei un analista di progetto. Rispondi ESCLUSIVAMENTE con un array JSON valido, "
    "senza markdown, senza spiegazioni."
)

_TEMPLATE = (
    "Analizza i seguenti testi numerati. Per ogni testo che contiene una decisione formale "
    "del progetto, includi un oggetto nell'array con: "
    '{{"indice": N, "descrizione": "...", "autore": "...", "data": "YYYY-MM-DD o null", "motivazione": "..."}}.\n'
    "Una decisione è una scelta GIÀ PRESA da chi ne ha l'autorità: approvata, deliberata, confermata, "
    "comunicata come definitiva. NON sono decisioni: richieste o proposte (anche del cliente), domande, "
    "richieste di conferma, ipotesi, opzioni ancora in valutazione, azioni da fare, semplici informazioni.\n"
    "Se nessun testo contiene decisioni, rispondi con []. Non includere testi senza decisioni.\n"
    "{gia_trovate}\n"
    "{testi}"
)


class DecisionTrackerAgent:
    def process(self, chunks: list[str], source_name: str, project_id: str, job=None) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        found = 0
        trovate: list[tuple[str, list]] = []   # (descrizione, embedding) già salvate per questo documento
        try:
            for i in range(0, len(chunks), _BATCH):
                if job and job.annullato:
                    break
                batch = chunks[i:i + _BATCH]
                testi = "\n\n".join(
                    f"[{j+1}] {c[:800]}" for j, c in enumerate(batch)
                )
                try:
                    raw = chat_completion(
                        messages=[{"role": "user", "content": _TEMPLATE.format(
                            testi=testi, gia_trovate=_elenco_gia_trovate(trovate))}],
                        system=_SYSTEM,
                        temperature=0.0,
                    )
                    decisioni = json.loads(raw.strip())
                    if not isinstance(decisioni, list):
                        decisioni = []
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
                            emb, emb_str = None, None
                        if emb and any(cosine_similarity(emb, e) >= _SOGLIA_DOPPIONE for _, e in trovate if e):
                            print(f"[DecisionTracker] Doppione scartato: {descrizione[:80]}", file=sys.stderr)
                            continue

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
                        trovate.append((descrizione, emb))
                except (json.JSONDecodeError, Exception) as e:
                    print(f"[DecisionTracker] Batch saltato: {e}", file=sys.stderr)
                if job:
                    job.avanza(analizzati=len(batch))

            conn.commit()
        finally:
            cursor.close()
            conn.close()
        return found


def _elenco_gia_trovate(trovate: list) -> str:
    if not trovate:
        return ""
    righe = "\n".join(f"- {d}" for d, _ in trovate)
    return f"Decisioni già registrate per questo documento: NON ripeterle, nemmeno con parole diverse.\n{righe}\n"
