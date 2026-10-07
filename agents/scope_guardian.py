import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from services.ai_core import chat_completion, get_embedding
from services.hana import get_connection, is_sqlite, cosine_similarity

_MAX_CONTRACT = 8000
_MAX_DOC = 6000
_TOP_K_CONTRACT = 8

_SYSTEM_ANALYST = (
    "Sei un esperto analista contrattuale per progetti IT. "
    "Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza markdown, senza testo aggiuntivo."
)

_PROMPT_QUERY = """\
Hai a disposizione le seguenti CLAUSOLE DEL CONTRATTO rilevanti per la domanda:

{contract}

---

L'utente ha posto questa DOMANDA O RICHIESTA:
"{query}"

Il tuo compito:
1. Analizza le clausole del contratto fornite.
2. Determina se la domanda/richiesta rientra nel perimetro contrattuale.
3. Se il contratto non copre esplicitamente il tema, valuta se è ragionevolmente riconducibile alle attività previste.

Rispondi con questo JSON esatto (nessun testo prima o dopo):
{{
  "fuori_scope": true,
  "confidenza": "alta",
  "elementi": ["elemento specifico fuori scope"],
  "clausole_rilevanti": ["breve citazione della clausola pertinente"],
  "motivazione": "spiegazione chiara e sintetica"
}}

Regole:
- "fuori_scope": true se la richiesta NON è coperta dal contratto, false se è coperta
- "elementi": lista degli elementi fuori perimetro (lista vuota se in scope)
- "confidenza": "alta" se le clausole sono esplicite, "media" se inferita, "bassa" se il contratto non è chiaro sul punto"""

_PROMPT_DOC = """\
CLAUSOLE CONTRATTO (estratto rilevante):
{contract}

---

DOCUMENTO/COMUNICAZIONE DA ANALIZZARE:
{document}

Analizza se questo documento contiene richieste o attività che esulano dallo scope contrattuale.

Rispondi con questo JSON esatto (nessun testo prima o dopo):
{{
  "fuori_scope": true,
  "confidenza": "alta",
  "elementi": ["elemento specifico fuori scope"],
  "clausole_rilevanti": ["breve citazione della clausola pertinente"],
  "motivazione": "spiegazione chiara e sintetica"
}}

Regole:
- "fuori_scope": true se il documento contiene richieste NON coperte dal contratto
- "elementi": lista vuota se tutto è nel perimetro
- "confidenza": "alta" se le clausole sono esplicite, "media" se inferita, "bassa" se il contratto non è chiaro"""


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


def _get_contract_chunks_semantic(query: str, project_id: str) -> str:
    """Retrieve contract chunks most relevant to the query using embeddings."""
    try:
        emb = get_embedding(query)
        emb_str = json.dumps(emb)
    except Exception:
        return _get_contract_chunks_fallback(project_id)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_sqlite():
            cursor.execute(
                "SELECT CONTENT, EMBEDDING FROM IPMP_DOCS "
                "WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%'",
                [project_id],
            )
            rows = cursor.fetchall()
            scored = []
            for row in rows:
                try:
                    doc_vec = json.loads(row[1])
                    score = cosine_similarity(emb, doc_vec)
                    scored.append((row[0], score))
                except Exception:
                    pass
            scored.sort(key=lambda x: x[1], reverse=True)
            chunks = [r[0] for r in scored[:_TOP_K_CONTRACT]]
        else:
            cursor.execute(
                f"SELECT TOP {_TOP_K_CONTRACT} CONTENT, "
                f"COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE "
                f"FROM IPMP_DOCS "
                f"WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%' "
                f"ORDER BY SCORE DESC",
                [emb_str, project_id],
            )
            chunks = [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()

    text = "\n---\n".join(chunks)
    return text[:_MAX_CONTRACT]


def _get_contract_chunks_fallback(project_id: str) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_sqlite():
            cursor.execute(
                "SELECT CONTENT FROM IPMP_DOCS "
                "WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%' "
                "ORDER BY CREATED_AT ASC LIMIT 10",
                [project_id],
            )
        else:
            cursor.execute(
                "SELECT TOP 10 CONTENT FROM IPMP_DOCS "
                "WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%' "
                "ORDER BY CREATED_AT ASC",
                [project_id],
            )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    text = "\n---\n".join(r[0] for r in rows)
    return text[:_MAX_CONTRACT]


def _save_alert(project_id: str, source_name: str, result: dict) -> int | None:
    elementi = result.get("elementi", [])
    motivazione = result.get("motivazione", "")
    confidenza = result.get("confidenza", "")
    alert_text = (
        f"FUORI SCOPE rilevato in: {source_name}\n"
        f"Confidenza: {confidenza}\n"
        f"Elementi: {', '.join(elementi)}\n"
        f"Motivazione: {motivazione}"
    )
    cr_draft = (
        f"BOZZA CHANGE REQUEST\n"
        f"Data: {date.today().isoformat()}\n"
        f"Documento sorgente: {source_name}\n\n"
        f"DESCRIZIONE VARIAZIONE RICHIESTA:\n"
        + "\n".join(f"- {e}" for e in elementi)
        + f"\n\nMOTIVAZIONE:\n{motivazione}\n\n"
        f"[Da completare con impatto su tempi, costi e perimetro contrattuale]"
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO IPMP_ALERTS (PROJECT_ID, SOURCE_NAME, ALERT_TEXT, CR_DRAFT) "
            "VALUES (?, ?, ?, ?)",
            [project_id, source_name, alert_text, cr_draft],
        )
        conn.commit()
        if is_sqlite():
            return cursor.lastrowid
        else:
            cursor.execute("SELECT IDENTITY_VAL_LOCAL() FROM DUMMY")
            return int(cursor.fetchone()[0])
    finally:
        cursor.close()
        conn.close()


def carica_contratto(file_path: str, project_id: str = None, original_name: str = None) -> int:
    from agents.extraction_agent import leggi_file, spezza
    from agents.indexing import IndexingAgent

    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")

    source_name = "CONTRATTO::" + (original_name or Path(file_path).name)
    full_text = leggi_file(file_path)
    chunks = spezza(full_text)
    n = IndexingAgent().indicizza(chunks, source_name, project_id)
    return n


class ScopeGuardian:
    def check_query(self, query: str, project_id: str) -> dict:
        """
        Verifica se una domanda utente (dalla chat) è nel perimetro contrattuale.
        Non salva alert — è una risposta informativa.
        """
        contract_ctx = _get_contract_chunks_semantic(query, project_id)
        if not contract_ctx.strip():
            return {
                "fuori_scope": False,
                "warning": "Nessun contratto caricato — impossibile verificare lo scope.",
                "motivazione": "Carica prima un contratto per abilitare il controllo di scope.",
                "elementi": [],
            }

        try:
            raw = chat_completion(
                messages=[{
                    "role": "user",
                    "content": _PROMPT_QUERY.format(contract=contract_ctx, query=query),
                }],
                system=_SYSTEM_ANALYST,
                temperature=0.0,
            )
            result = _parse_json(raw)
        except Exception as e:
            print(f"[ScopeGuardian.check_query] Errore: {e}", file=sys.stderr)
            return {"fuori_scope": False, "error": str(e), "elementi": []}

        result.setdefault("alert_id", None)
        return result

    def check(self, document_text: str, source_name: str, project_id: str) -> dict:
        """
        Verifica un documento/comunicazione ingerita contro il contratto.
        Salva un alert in DB se fuori scope.
        """
        contract_ctx = _get_contract_chunks_semantic(document_text[:500], project_id)
        if not contract_ctx.strip():
            return {"fuori_scope": False, "warning": "Nessun contratto caricato — Scope Guardian disattivo.", "elementi": []}

        doc_trunc = document_text[:_MAX_DOC]
        try:
            raw = chat_completion(
                messages=[{
                    "role": "user",
                    "content": _PROMPT_DOC.format(contract=contract_ctx, document=doc_trunc),
                }],
                system=_SYSTEM_ANALYST,
                temperature=0.0,
            )
            result = _parse_json(raw)
        except Exception as e:
            print(f"[ScopeGuardian.check] Errore LLM: {e}", file=sys.stderr)
            return {"fuori_scope": False, "error": str(e), "elementi": []}

        alert_id = None
        if result.get("fuori_scope"):
            try:
                alert_id = _save_alert(project_id, source_name, result)
            except Exception as e:
                print(f"[ScopeGuardian] Errore salvataggio alert: {e}", file=sys.stderr)

        result["alert_id"] = alert_id
        return result

    def get_alerts(self, project_id: str, unhandled_only: bool = True) -> list[dict]:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            if unhandled_only:
                cursor.execute(
                    "SELECT ID, SOURCE_NAME, ALERT_TEXT, CR_DRAFT, IS_HANDLED, CREATED_AT "
                    "FROM IPMP_ALERTS WHERE PROJECT_ID=? AND IS_HANDLED=0 ORDER BY CREATED_AT DESC",
                    [project_id],
                )
            else:
                cursor.execute(
                    "SELECT ID, SOURCE_NAME, ALERT_TEXT, CR_DRAFT, IS_HANDLED, CREATED_AT "
                    "FROM IPMP_ALERTS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC",
                    [project_id],
                )
            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

        return [
            {
                "id": r[0],
                "source": r[1],
                "alert_text": r[2],
                "cr_draft": r[3],
                "is_handled": bool(r[4]),
                "created_at": str(r[5]),
            }
            for r in rows
        ]

    def mark_handled(self, alert_id: int) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE IPMP_ALERTS SET IS_HANDLED=1 WHERE ID=?", [alert_id])
            conn.commit()
        finally:
            cursor.close()
            conn.close()
