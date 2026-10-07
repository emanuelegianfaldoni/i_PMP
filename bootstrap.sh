#!/bin/bash
# i-PMP Bootstrap — incolla questo script nel terminale BAS ed esegui:
#   bash bootstrap.sh
set -e

# NOTA: i valori nel blocco .env qui sotto sono placeholder.
# Compilali con le credenziali reali prese dalla service key su SAP BTP Cockpit.

echo "🚀 Creazione progetto i-PMP in ~/projects/ipmp ..."
mkdir -p ~/projects/ipmp/{services,agents,app,sql}
cd ~/projects/ipmp

# ── .env ─────────────────────────────────────────────────────────────────────
cat > .env << 'ENDENV'
# --- SAP AI Core ---
AICORE_CLIENT_ID=sb-<uuid>|aicore!b540
AICORE_CLIENT_SECRET=<secret>
AICORE_AUTH_URL=https://<tenant>.authentication.eu10.hana.ondemand.com
AICORE_BASE_URL=https://api.ai.prod.eu-central-1.aws.ml.hana.ondemand.com
AICORE_RESOURCE_GROUP=i-pmp

# --- Deployment ID (compilare dopo setup_deployments.py) ---
CHAT_DEPLOYMENT_ID=<da-compilare>
EMBED_DEPLOYMENT_ID=<da-compilare>

# --- Modelli ---
CHAT_MODEL=anthropic--claude-4.6-sonnet
EMBED_MODEL=text-embedding-3-large

# --- HANA Cloud ---
HANA_HOST=<uuid>.hna2.prod-eu10.hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=<hana-user>
HANA_PASSWORD=<hana-password>

PROJECT_ID=demo
EMBED_DIMS=3072
ENDENV

# ── .gitignore ────────────────────────────────────────────────────────────────
cat > .gitignore << 'ENDGIT'
.env
__pycache__/
*.pyc
.venv/
venv/
*.docx
ENDGIT

# ── requirements.txt ──────────────────────────────────────────────────────────
cat > requirements.txt << 'ENDREQ'
hdbcli>=2.19.0
requests>=2.31.0
python-dotenv>=1.0.0
fastapi>=0.111.0
uvicorn>=0.29.0
streamlit>=1.35.0
pdfplumber>=0.11.0
python-docx>=1.1.0
extract-msg>=0.47.0
python-pptx>=0.6.23
schedule>=1.2.0
watchdog>=4.0.0
python-multipart>=0.0.9
httpx>=0.27.0
ENDREQ

# ── sql/schema.sql ────────────────────────────────────────────────────────────
cat > sql/schema.sql << 'ENDSQL'
CREATE TABLE IF NOT EXISTS IPMP_DOCS (
    ID          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    PROJECT_ID  NVARCHAR(50)  NOT NULL,
    SOURCE_NAME NVARCHAR(500),
    CONTENT     NCLOB,
    EMBEDDING   REAL_VECTOR(3072),
    CREATED_AT  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS IPMP_DECISIONS (
    ID             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    PROJECT_ID     NVARCHAR(50) NOT NULL,
    DESCRIZIONE    NCLOB,
    AUTORE         NVARCHAR(200),
    DATA_DECISIONE DATE,
    MOTIVAZIONE    NCLOB,
    SOURCE_NAME    NVARCHAR(500),
    CREATED_AT     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS IPMP_CONTEXT (
    ID          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    PROJECT_ID  NVARCHAR(50)  NOT NULL,
    SOURCE_NAME NVARCHAR(500),
    SUMMARY     NCLOB,
    EMBEDDING   REAL_VECTOR(3072),
    CREATED_AT  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS IPMP_ALERTS (
    ID          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    PROJECT_ID  NVARCHAR(50) NOT NULL,
    SOURCE_NAME NVARCHAR(500),
    ALERT_TEXT  NCLOB,
    CR_DRAFT    NCLOB,
    IS_HANDLED  TINYINT DEFAULT 0,
    CREATED_AT  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS IPMP_DIGESTS (
    ID          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    PROJECT_ID  NVARCHAR(50) NOT NULL,
    DIGEST_TEXT NCLOB,
    CREATED_AT  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE VECTOR INDEX IPMP_DOCS_VEC_IDX    ON IPMP_DOCS(EMBEDDING)    SIMILARITY FUNCTION COSINE;
CREATE VECTOR INDEX IPMP_CONTEXT_VEC_IDX ON IPMP_CONTEXT(EMBEDDING) SIMILARITY FUNCTION COSINE;
ENDSQL

# ── services/__init__.py ──────────────────────────────────────────────────────
touch services/__init__.py
touch agents/__init__.py
touch app/__init__.py

# ── services/hana.py ──────────────────────────────────────────────────────────
cat > services/hana.py << 'ENDHANA'
import os
from hdbcli import dbapi

def get_connection() -> dbapi.Connection:
    return dbapi.connect(
        address=os.getenv("HANA_HOST"),
        port=int(os.getenv("HANA_PORT", 443)),
        user=os.getenv("HANA_USER"),
        password=os.getenv("HANA_PASSWORD"),
        encrypt=True,
        sslValidateCertificate=False,
    )
ENDHANA

# ── services/ai_core.py ───────────────────────────────────────────────────────
cat > services/ai_core.py << 'ENDAICORE'
import os, json, time, requests

_token_cache: dict = {"token": None, "expires_at": 0.0}

def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    r = requests.post(
        os.getenv("AICORE_AUTH_URL", "").rstrip("/") + "/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(os.getenv("AICORE_CLIENT_ID"), os.getenv("AICORE_CLIENT_SECRET")),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return _token_cache["token"]

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "AI-Resource-Group": os.getenv("AICORE_RESOURCE_GROUP", "i-pmp"),
        "Content-Type": "application/json",
    }

def chat_completion(messages: list, system: str = "", temperature: float = 0.2) -> str:
    dep = os.getenv("CHAT_DEPLOYMENT_ID", "")
    if not dep or dep == "<da-compilare>":
        raise EnvironmentError("CHAT_DEPLOYMENT_ID non configurato nel .env")
    base = os.getenv("AICORE_BASE_URL", "").rstrip("/")
    url = f"{base}/v2/inference/deployments/{dep}/chat/completions"
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)
    r = requests.post(url, headers=_headers(),
                      json={"model": os.getenv("CHAT_MODEL"), "messages": msgs,
                            "temperature": temperature, "max_tokens": 4096},
                      timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def get_embedding(text: str) -> list:
    dep = os.getenv("EMBED_DEPLOYMENT_ID", "")
    if not dep or dep == "<da-compilare>":
        raise EnvironmentError("EMBED_DEPLOYMENT_ID non configurato nel .env")
    base = os.getenv("AICORE_BASE_URL", "").rstrip("/")
    url = f"{base}/v2/inference/deployments/{dep}/embeddings"
    r = requests.post(url, headers=_headers(),
                      json={"input": text, "model": os.getenv("EMBED_MODEL")},
                      timeout=30)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]
ENDAICORE

# ── agents/ingestion.py ───────────────────────────────────────────────────────
cat > agents/ingestion.py << 'ENDING'
import os, sys, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def leggi_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(file_path).paragraphs if p.text.strip())
    if ext == ".msg":
        import extract_msg
        msg = extract_msg.Message(file_path)
        return "\n".join(p for p in [msg.subject or "", msg.body or ""] if p)
    if ext == ".pptx":
        from pptx import Presentation
        texts = []
        for slide in Presentation(file_path).slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    texts.extend(p.text for p in shape.text_frame.paragraphs)
        return "\n".join(t for t in texts if t.strip())
    return Path(file_path).read_text(encoding="utf-8", errors="replace")

def spezza(text: str, chunk_size: int = 600, overlap: int = 80) -> list:
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

def pipeline_ingestion(file_path: str, project_id: str = None) -> dict:
    from agents.indexing import IndexingAgent
    from agents.context_agent import ContextAgent
    from agents.decision_tracker import DecisionTrackerAgent
    from agents.scope_guardian import ScopeGuardian
    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")
    source_name = Path(file_path).name
    full_text = leggi_file(file_path)
    chunks = spezza(full_text)
    results = {"source": source_name, "chunks": len(chunks)}
    tasks = {
        "indexing":  lambda: ("indexing",  {"chunks_indexed": IndexingAgent().indicizza(chunks, source_name, project_id)}),
        "context":   lambda: ("context",   {"ok": ContextAgent().process(full_text, source_name, project_id)}),
        "decisions": lambda: ("decisions", {"found": DecisionTrackerAgent().process(chunks, source_name, project_id)}),
        "scope":     lambda: ("scope",     ScopeGuardian().check(full_text, source_name, project_id)),
    }
    def run(fn):
        try:
            return fn()
        except Exception as e:
            return (fn.__qualname__, {"error": str(e)})
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run, fn) for fn in tasks.values()]
        for f in as_completed(futures):
            key, val = f.result()
            results[key] = val
    return results

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    result = pipeline_ingestion(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
ENDING

# ── agents/indexing.py ────────────────────────────────────────────────────────
cat > agents/indexing.py << 'ENDINDX'
import json, sys
from services.ai_core import get_embedding
from services.hana import get_connection

class IndexingAgent:
    def indicizza(self, chunks: list, source_name: str, project_id: str) -> int:
        conn = get_connection()
        cursor = conn.cursor()
        indexed = 0
        try:
            for chunk in chunks:
                try:
                    emb_str = json.dumps(get_embedding(chunk))
                    cursor.execute(
                        "INSERT INTO IPMP_DOCS (PROJECT_ID, SOURCE_NAME, CONTENT, EMBEDDING) "
                        "VALUES (?, ?, ?, TO_REAL_VECTOR(?))",
                        [project_id, source_name, chunk, emb_str],
                    )
                    indexed += 1
                except Exception as e:
                    print(f"[IndexingAgent] chunk saltato: {e}", file=sys.stderr)
            conn.commit()
        finally:
            cursor.close(); conn.close()
        return indexed
ENDINDX

# ── agents/context_agent.py ───────────────────────────────────────────────────
cat > agents/context_agent.py << 'ENDCTX'
import json, sys
from services.ai_core import chat_completion, get_embedding
from services.hana import get_connection

_PROMPT = ("Genera un riassunto strutturato di questo documento in circa 300 parole, "
           "catturando il tema principale, i punti chiave, i partecipanti se presenti "
           "e le conclusioni. Rispondi in italiano.\n\n---\n")

class ContextAgent:
    def process(self, full_text: str, source_name: str, project_id: str) -> bool:
        try:
            summary = chat_completion(messages=[{"role": "user", "content": _PROMPT + full_text[:8000]}])
            emb_str = json.dumps(get_embedding(summary))
            conn = get_connection(); cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO IPMP_CONTEXT (PROJECT_ID, SOURCE_NAME, SUMMARY, EMBEDDING) "
                    "VALUES (?, ?, ?, TO_REAL_VECTOR(?))",
                    [project_id, source_name, summary, emb_str])
                conn.commit()
            finally:
                cursor.close(); conn.close()
            return True
        except Exception as e:
            print(f"[ContextAgent] {e}", file=sys.stderr)
            return False
ENDCTX

# ── agents/decision_tracker.py ────────────────────────────────────────────────
cat > agents/decision_tracker.py << 'ENDDEC'
import json, sys
from datetime import date
from services.ai_core import chat_completion
from services.hana import get_connection

_SYS = "Sei un analista di progetto. Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza markdown."
_TPL = ('Questo testo contiene una decisione formale?\n'
        'Se si: {{"contiene_decisione":true,"descrizione":"...","autore":"...","data":"YYYY-MM-DD o null","motivazione":"..."}}\n'
        'Se no: {{"contiene_decisione":false}}\n\nTESTO:\n{chunk}')

class DecisionTrackerAgent:
    def process(self, chunks: list, source_name: str, project_id: str) -> int:
        conn = get_connection(); cursor = conn.cursor(); found = 0
        try:
            for chunk in chunks:
                try:
                    raw = chat_completion(
                        messages=[{"role": "user", "content": _TPL.format(chunk=chunk[:1200])}],
                        system=_SYS, temperature=0.0)
                    data = json.loads(raw.strip())
                    if not data.get("contiene_decisione"):
                        continue
                    dd = data.get("data")
                    try:
                        dd = date.fromisoformat(dd) if dd else None
                    except ValueError:
                        dd = None
                    cursor.execute(
                        "INSERT INTO IPMP_DECISIONS (PROJECT_ID,DESCRIZIONE,AUTORE,DATA_DECISIONE,MOTIVAZIONE,SOURCE_NAME) "
                        "VALUES (?,?,?,?,?,?)",
                        [project_id, data.get("descrizione",""), data.get("autore",""),
                         dd, data.get("motivazione",""), source_name])
                    found += 1
                except Exception as e:
                    print(f"[DecisionTracker] chunk saltato: {e}", file=sys.stderr)
            conn.commit()
        finally:
            cursor.close(); conn.close()
        return found
ENDDEC

# ── agents/scope_guardian.py ──────────────────────────────────────────────────
cat > agents/scope_guardian.py << 'ENDSCOPE'
import json, os, sys
from pathlib import Path
from services.ai_core import chat_completion
from services.hana import get_connection

_SYS = "Sei un analista contrattuale. Rispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza markdown."
_PROMPT = ('CLAUSOLE CONTRATTO:\n{contract}\n\nDOCUMENTO:\n{document}\n\n'
           'Questa comunicazione contiene richieste fuori scope contrattuale?\n'
           'Rispondi: {{"fuori_scope":true/false,"elementi":["..."],"motivazione":"..."}}')

def carica_contratto(file_path: str, project_id: str = None) -> int:
    from agents.ingestion import leggi_file, spezza
    from agents.indexing import IndexingAgent
    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")
    source_name = "CONTRATTO::" + Path(file_path).name
    return IndexingAgent().indicizza(spezza(leggi_file(file_path)), source_name, project_id)

def _contract_ctx(project_id: str) -> str:
    conn = get_connection(); cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT TOP 10 CONTENT FROM IPMP_DOCS "
            "WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%' ORDER BY CREATED_AT ASC",
            [project_id])
        rows = cursor.fetchall()
    finally:
        cursor.close(); conn.close()
    text = "\n---\n".join(r[0] for r in rows)
    return text[:4000]

class ScopeGuardian:
    def check(self, document_text: str, source_name: str, project_id: str) -> dict:
        ctx = _contract_ctx(project_id)
        if not ctx.strip():
            return {"fuori_scope": False, "warning": "Nessun contratto caricato — Scope Guardian disattivo."}
        try:
            raw = chat_completion(
                messages=[{"role": "user", "content": _PROMPT.format(
                    contract=ctx, document=document_text[:6000])}],
                system=_SYS, temperature=0.0)
            result = json.loads(raw.strip())
        except Exception as e:
            return {"fuori_scope": False, "error": str(e)}
        if result.get("fuori_scope"):
            elementi = result.get("elementi", [])
            alert_text = f"FUORI SCOPE in: {source_name}\nElementi: {', '.join(elementi)}\n{result.get('motivazione','')}"
            cr_draft = (f"BOZZA CHANGE REQUEST\nData: {__import__('datetime').date.today()}\n"
                        f"Sorgente: {source_name}\n\nVARIAZIONE:\n" +
                        "\n".join(f"- {e}" for e in elementi) +
                        f"\n\nMOTIVAZIONE:\n{result.get('motivazione','')}")
            conn = get_connection(); cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO IPMP_ALERTS (PROJECT_ID,SOURCE_NAME,ALERT_TEXT,CR_DRAFT) VALUES (?,?,?,?)",
                    [project_id, source_name, alert_text, cr_draft])
                conn.commit()
                cursor.execute("SELECT IDENTITY_VAL_LOCAL() FROM DUMMY")
                result["alert_id"] = int(cursor.fetchone()[0])
            finally:
                cursor.close(); conn.close()
        return result

    def get_alerts(self, project_id: str, unhandled_only: bool = True) -> list:
        conn = get_connection(); cursor = conn.cursor()
        try:
            q = ("SELECT ID,SOURCE_NAME,ALERT_TEXT,CR_DRAFT,IS_HANDLED,CREATED_AT FROM IPMP_ALERTS "
                 "WHERE PROJECT_ID=?" + (" AND IS_HANDLED=0" if unhandled_only else "") +
                 " ORDER BY CREATED_AT DESC")
            cursor.execute(q, [project_id])
            rows = cursor.fetchall()
        finally:
            cursor.close(); conn.close()
        return [{"id":r[0],"source":r[1],"alert_text":r[2],"cr_draft":r[3],
                 "is_handled":bool(r[4]),"created_at":str(r[5])} for r in rows]

    def mark_handled(self, alert_id: int):
        conn = get_connection(); cursor = conn.cursor()
        try:
            cursor.execute("UPDATE IPMP_ALERTS SET IS_HANDLED=1 WHERE ID=?", [alert_id])
            conn.commit()
        finally:
            cursor.close(); conn.close()
ENDSCOPE

# ── agents/retrieval.py ───────────────────────────────────────────────────────
cat > agents/retrieval.py << 'ENDRET'
import json
from services.ai_core import get_embedding
from services.hana import get_connection

class RetrievalAgent:
    def cerca(self, query: str, project_id: str, top_k: int = 5) -> list:
        emb_str = json.dumps(get_embedding(query))
        conn = get_connection(); cursor = conn.cursor(); results = []
        try:
            cursor.execute(
                f"SELECT TOP {top_k} ID,SOURCE_NAME,CONTENT,CREATED_AT,"
                f"COSINE_SIMILARITY(EMBEDDING,TO_REAL_VECTOR(?)) AS SCORE "
                f"FROM IPMP_DOCS WHERE PROJECT_ID=? AND SOURCE_NAME NOT LIKE 'CONTRATTO::%' "
                f"ORDER BY SCORE DESC", [emb_str, project_id])
            for r in cursor.fetchall():
                results.append({"id":r[0],"source":r[1],"content":r[2],"created_at":str(r[3]),"score":float(r[4]),"type":"doc"})
            cursor.execute(
                f"SELECT TOP 3 ID,SOURCE_NAME,SUMMARY,CREATED_AT,"
                f"COSINE_SIMILARITY(EMBEDDING,TO_REAL_VECTOR(?)) AS SCORE "
                f"FROM IPMP_CONTEXT WHERE PROJECT_ID=? ORDER BY SCORE DESC",
                [emb_str, project_id])
            for r in cursor.fetchall():
                results.append({"id":r[0],"source":r[1],"content":r[2],"created_at":str(r[3]),"score":float(r[4]),"type":"context"})
            cursor.execute(
                "SELECT TOP 5 ID,SOURCE_NAME,DESCRIZIONE,DATA_DECISIONE,AUTORE FROM IPMP_DECISIONS "
                "WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC", [project_id])
            for r in cursor.fetchall():
                results.append({"id":r[0],"source":r[1] or "Decision Log",
                                "content":f"Decisione: {r[2]} (Autore: {r[4]}, Data: {r[3]})",
                                "created_at":str(r[3]),"score":0.5,"type":"decision"})
        finally:
            cursor.close(); conn.close()
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k + 5]
ENDRET

# ── agents/synthesis.py ───────────────────────────────────────────────────────
cat > agents/synthesis.py << 'ENDSYN'
from services.ai_core import chat_completion

_SYS = ("Sei un assistente di progetto. Rispondi SOLO usando le informazioni nel contesto. "
        "Cita sempre la fonte. Se le info non bastano, dillo. Rispondi in italiano.")

class SynthesisAgent:
    def genera(self, domanda: str, chunks: list) -> dict:
        fonti = set()
        parts = []
        for c in chunks:
            parts.append(f"[Fonte: {c.get('source','?')} — {c.get('created_at','')}]\n{c.get('content','')}")
            fonti.add(c.get("source", "?"))
        context = "\n\n".join(parts)
        risposta = chat_completion(
            messages=[{"role":"user","content":f"Contesto:\n{context}\n\nDomanda: {domanda}"}],
            system=_SYS, temperature=0.2)
        return {"risposta": risposta, "fonti": sorted(fonti)}
ENDSYN

# ── agents/orchestrator.py ────────────────────────────────────────────────────
cat > agents/orchestrator.py << 'ENDORCH'
import os
from services.ai_core import chat_completion
from services.hana import get_connection
from agents.retrieval import RetrievalAgent
from agents.synthesis import SynthesisAgent
from agents.scope_guardian import ScopeGuardian

_SYS = ("Classifica l'intent. Rispondi con UNA parola: MEMORY, SCOPE o DECISIONS. "
        "MEMORY=domanda su documenti/progetto. SCOPE=verifica contratto. DECISIONS=log decisioni.")

class Orchestrator:
    def smista(self, messaggio: str, project_id: str = None) -> dict:
        if project_id is None:
            project_id = os.getenv("PROJECT_ID", "demo")
        intent = chat_completion(
            messages=[{"role":"user","content":messaggio}],
            system=_SYS, temperature=0.0).strip().upper()

        if "SCOPE" in intent:
            r = ScopeGuardian().check(messaggio, "on-demand", project_id)
            if r.get("fuori_scope"):
                resp = f"FUORI SCOPE rilevato.\nElementi: {', '.join(r.get('elementi',[]))}\n{r.get('motivazione','')}"
            else:
                resp = "La richiesta sembra nel perimetro contrattuale."
            return {"tipo":"SCOPE","risposta":resp,"fonti":[],"raw":r}

        if "DECISIONS" in intent:
            conn = get_connection(); cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT TOP 10 DESCRIZIONE,AUTORE,DATA_DECISIONE,MOTIVAZIONE FROM IPMP_DECISIONS "
                    "WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC", [project_id])
                rows = cursor.fetchall()
            finally:
                cursor.close(); conn.close()
            if not rows:
                resp = "Nessuna decisione registrata."
            else:
                resp = "**Decisioni:**\n\n" + "\n\n".join(
                    f"* {r[0]} (Autore: {r[1]}, Data: {r[2]})\n  {r[3]}" for r in rows)
            return {"tipo":"DECISIONS","risposta":resp,"fonti":["IPMP_DECISIONS"],"raw":{}}

        chunks = RetrievalAgent().cerca(messaggio, project_id)
        result = SynthesisAgent().genera(messaggio, chunks)
        return {"tipo":"MEMORY","risposta":result["risposta"],"fonti":result["fonti"],"raw":{}}
ENDORCH

# ── agents/monitoring.py ──────────────────────────────────────────────────────
cat > agents/monitoring.py << 'ENDMON'
import os, sys, threading, schedule, time
from services.ai_core import chat_completion
from services.hana import get_connection

_SYS = "Sei un project manager. Genera un digest operativo settimanale in italiano, strutturato e conciso."
_PROMPT = ("Genera un digest operativo settimanale con sezioni:\n"
           "1. Documenti processati\n2. Decisioni recenti\n3. Alert Scope aperti\n"
           "4. Task aperti\n5. Scadenze imminenti\n\nDATI:\n{dati}")

class MonitoringAgent:
    def genera_digest(self, project_id: str = None) -> str:
        if project_id is None:
            project_id = os.getenv("PROJECT_ID", "demo")
        conn = get_connection(); cursor = conn.cursor(); sezioni = []
        try:
            cursor.execute("SELECT SOURCE_NAME,CREATED_AT FROM IPMP_DOCS WHERE PROJECT_ID=? "
                           "AND CREATED_AT>=ADD_DAYS(NOW(),-7) AND SOURCE_NAME NOT LIKE 'CONTRATTO::%' "
                           "GROUP BY SOURCE_NAME,CREATED_AT ORDER BY CREATED_AT DESC", [project_id])
            docs = [f"  - {r[0]} ({r[1]})" for r in cursor.fetchall()]
            sezioni.append("DOCUMENTI:\n" + ("\n".join(docs) if docs else "  Nessuno"))
            cursor.execute("SELECT DESCRIZIONE,AUTORE,DATA_DECISIONE FROM IPMP_DECISIONS "
                           "WHERE PROJECT_ID=? AND CREATED_AT>=ADD_DAYS(NOW(),-7) ORDER BY CREATED_AT DESC", [project_id])
            dec = [f"  - {r[0]} (Autore: {r[1]}, Data: {r[2]})" for r in cursor.fetchall()]
            sezioni.append("DECISIONI:\n" + ("\n".join(dec) if dec else "  Nessuna"))
            cursor.execute("SELECT SOURCE_NAME,ALERT_TEXT FROM IPMP_ALERTS WHERE PROJECT_ID=? AND IS_HANDLED=0", [project_id])
            alerts = [f"  - [{r[0]}] {r[1][:120]}" for r in cursor.fetchall()]
            sezioni.append("ALERT APERTI:\n" + ("\n".join(alerts) if alerts else "  Nessuno"))
        finally:
            cursor.close(); conn.close()
        try:
            digest = chat_completion(
                messages=[{"role":"user","content":_PROMPT.format(dati="\n\n".join(sezioni))}],
                system=_SYS, temperature=0.3)
        except Exception as e:
            digest = f"Errore: {e}\n\nDati grezzi:\n" + "\n\n".join(sezioni)
        conn = get_connection(); cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO IPMP_DIGESTS (PROJECT_ID,DIGEST_TEXT) VALUES (?,?)", [project_id, digest])
            conn.commit()
        finally:
            cursor.close(); conn.close()
        return digest

    def get_latest_digest(self, project_id: str = None) -> str:
        if project_id is None:
            project_id = os.getenv("PROJECT_ID", "demo")
        conn = get_connection(); cursor = conn.cursor()
        try:
            cursor.execute("SELECT TOP 1 DIGEST_TEXT FROM IPMP_DIGESTS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC", [project_id])
            row = cursor.fetchone()
        finally:
            cursor.close(); conn.close()
        return row[0] if row else None

def start_scheduler(project_id: str = None) -> threading.Thread:
    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")
    schedule.every().sunday.at("23:00").do(lambda: MonitoringAgent().genera_digest(project_id))
    def run():
        while True:
            schedule.run_pending(); time.sleep(60)
    t = threading.Thread(target=run, daemon=True); t.start(); return t
ENDMON

# ── agents/playbook.py ────────────────────────────────────────────────────────
cat > agents/playbook.py << 'ENDPLAY'
import os, sys
from datetime import date
from docx import Document as DocxDocument
from services.ai_core import chat_completion
from services.hana import get_connection

_SYS = ("Sei un knowledge manager. Genera un playbook anonimizzato in italiano. "
        "Rimuovi TUTTI i nomi di persone, cliente, email e riferimenti identificativi.")
_PROMPT = ("Genera un playbook con 7 sezioni:\n"
           "1. CONTESTO E OBIETTIVI (senza nominare il cliente)\n"
           "2. APPROCCIO METODOLOGICO\n3. DECISIONI CHIAVE E MOTIVAZIONI\n"
           "4. PROBLEMI E SOLUZIONI (Problema -> Soluzione -> Risultato)\n"
           "5. PATTERN E LESSONS LEARNED\n6. RACCOMANDAZIONI\n7. STACK TECNOLOGICO\n\n"
           "MEMORIA:\n{memoria}")

class PlaybookAgent:
    def genera(self, project_id: str = None, output_path: str = "playbook.docx") -> str:
        if project_id is None:
            project_id = os.getenv("PROJECT_ID", "demo")
        conn = get_connection(); cursor = conn.cursor(); parts = []
        try:
            cursor.execute("SELECT SOURCE_NAME,CONTENT FROM IPMP_DOCS WHERE PROJECT_ID=? "
                           "AND SOURCE_NAME NOT LIKE 'CONTRATTO::%' ORDER BY CREATED_AT", [project_id])
            for r in cursor.fetchall():
                parts.append(f"[{r[0]}] {r[1]}")
            cursor.execute("SELECT DESCRIZIONE,AUTORE,DATA_DECISIONE,MOTIVAZIONE FROM IPMP_DECISIONS "
                           "WHERE PROJECT_ID=? ORDER BY CREATED_AT", [project_id])
            for r in cursor.fetchall():
                parts.append(f"[DECISIONE] {r[0]} (Autore: {r[1]}, Data: {r[2]}) -- {r[3]}")
        finally:
            cursor.close(); conn.close()
        memoria = "\n\n".join(parts)[:20000]
        text = chat_completion(
            messages=[{"role":"user","content":_PROMPT.format(memoria=memoria)}],
            system=_SYS, temperature=0.3)
        doc = DocxDocument()
        doc.add_heading(f"Project Playbook -- {date.today()}", 0)
        doc.add_paragraph(f"Progetto: {project_id} | Generato da i-PMP")
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if s[0].isdigit() and "." in s[:3]:
                doc.add_heading(s, level=1)
            elif s.startswith(("-","*","•")):
                doc.add_paragraph(s, style="List Bullet")
            else:
                doc.add_paragraph(s)
        doc.save(output_path)
        return output_path
ENDPLAY

# ── app/main.py ───────────────────────────────────────────────────────────────
cat > app/main.py << 'ENDMAIN'
import os, sys, tempfile, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from agents.ingestion import pipeline_ingestion
from agents.orchestrator import Orchestrator
from agents.scope_guardian import ScopeGuardian, carica_contratto
from agents.monitoring import MonitoringAgent, start_scheduler
from agents.playbook import PlaybookAgent

app = FastAPI(title="i-PMP API", version="1.0")
PROJECT_ID = os.getenv("PROJECT_ID", "demo")
_orch = Orchestrator(); _scope = ScopeGuardian(); _mon = MonitoringAgent()

@app.on_event("startup")
def startup():
    start_scheduler(PROJECT_ID)

class QueryRequest(BaseModel):
    domanda: str
    project_id: str = None

class ScopeRequest(BaseModel):
    comunicazione: str
    project_id: str = None

@app.get("/health")
def health():
    return {"status": "ok", "project_id": PROJECT_ID}

@app.post("/ingest")
async def ingest(file: UploadFile = File(...), project_id: str = Query(None)):
    pid = project_id or PROJECT_ID
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp); tmp_path = tmp.name
    try:
        result = pipeline_ingestion(tmp_path, pid)
    finally:
        os.unlink(tmp_path)
    return result

@app.post("/load-contract")
async def load_contract(file: UploadFile = File(...), project_id: str = Query(None)):
    pid = project_id or PROJECT_ID
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp); tmp_path = tmp.name
    try:
        n = carica_contratto(tmp_path, pid)
    finally:
        os.unlink(tmp_path)
    return {"chunks_loaded": n, "message": f"Contratto caricato: {n} chunk indicizzati"}

@app.post("/query")
def query(body: QueryRequest):
    try:
        return _orch.smista(body.domanda, body.project_id or PROJECT_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scope-check")
def scope_check(body: ScopeRequest):
    try:
        return _scope.check(body.comunicazione, "on-demand", body.project_id or PROJECT_ID)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/alerts")
def get_alerts(project_id: str = Query(None), unhandled_only: bool = True):
    return _scope.get_alerts(project_id or PROJECT_ID, unhandled_only)

@app.post("/alerts/{alert_id}/handle")
def handle_alert(alert_id: int):
    _scope.mark_handled(alert_id); return {"ok": True}

@app.get("/decisions")
def get_decisions(project_id: str = Query(None)):
    from services.hana import get_connection
    pid = project_id or PROJECT_ID
    conn = get_connection(); cursor = conn.cursor()
    try:
        cursor.execute("SELECT ID,DESCRIZIONE,AUTORE,DATA_DECISIONE,MOTIVAZIONE,SOURCE_NAME,CREATED_AT "
                       "FROM IPMP_DECISIONS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC", [pid])
        rows = cursor.fetchall()
    finally:
        cursor.close(); conn.close()
    return [{"id":r[0],"descrizione":r[1],"autore":r[2],"data":str(r[3]),
             "motivazione":r[4],"source":r[5],"created_at":str(r[6])} for r in rows]

@app.get("/weekly-digest")
def get_digest(project_id: str = Query(None)):
    text = _mon.get_latest_digest(project_id or PROJECT_ID)
    if text is None:
        return {"digest": None, "message": "Nessun digest. Usa POST /generate-digest"}
    return {"digest": text}

@app.post("/generate-digest")
def gen_digest(project_id: str = Query(None)):
    return {"digest": _mon.genera_digest(project_id or PROJECT_ID)}

@app.post("/playbook")
def playbook(project_id: str = Query(None)):
    pid = project_id or PROJECT_ID
    path = f"/tmp/playbook_{pid}.docx"
    try:
        PlaybookAgent().genera(pid, path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return FileResponse(path, filename="playbook.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
ENDMAIN

# ── app/app.py ────────────────────────────────────────────────────────────────
cat > app/app.py << 'ENDAPP'
import os, sys, requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="i-PMP", layout="wide", page_icon="🧠")
st.sidebar.markdown("## 🧠 i-PMP")
st.sidebar.markdown("*Intelligent Project Memory Platform*")
st.sidebar.markdown("---")
page = st.sidebar.radio("Navigazione", ["🏠 Home","💬 Chat","📄 Documenti","🛡 Scope & CR","📘 Playbook"])

def api(method, path, **kwargs):
    try:
        r = getattr(requests, method)(BACKEND.rstrip("/") + path, timeout=120, **kwargs)
        r.raise_for_status(); return r
    except requests.exceptions.ConnectionError:
        st.error("Backend non raggiungibile. Avvia uvicorn prima."); return None
    except requests.exceptions.HTTPError as e:
        st.error(f"Errore API: {e.response.text}"); return None

if page == "🏠 Home":
    st.title("🏠 Dashboard di Progetto")
    col1, col2 = st.columns([2,1])
    with col1:
        st.subheader("📋 Weekly Digest")
        r = api("get", "/weekly-digest")
        if r:
            d = r.json()
            st.markdown(d["digest"] if d.get("digest") else d.get("message",""))
        if st.button("🔄 Genera Digest Ora"):
            with st.spinner("Generazione..."):
                r = api("post", "/generate-digest")
            if r: st.markdown(r.json().get("digest",""))
    with col2:
        st.subheader("🔔 Alert Scope")
        r = api("get", "/alerts")
        if r:
            alerts = r.json()
            if not alerts: st.success("Nessun alert aperto")
            for a in alerts:
                with st.expander(f"⚠️ {a['source']}"):
                    st.markdown(a["alert_text"])
                    if st.button("✅ Gestito", key=f"h{a['id']}"):
                        api("post", f"/alerts/{a['id']}/handle"); st.rerun()

elif page == "💬 Chat":
    st.title("💬 Chat — Memoria del Progetto")
    if "messages" not in st.session_state: st.session_state.messages = []
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("fonti"): st.caption("Fonti: " + " · ".join(m["fonti"]))
    if domanda := st.chat_input("Scrivi la tua domanda..."):
        st.session_state.messages.append({"role":"user","content":domanda})
        with st.chat_message("user"): st.markdown(domanda)
        with st.chat_message("assistant"):
            with st.spinner("Ricerca nella memoria..."):
                r = api("post", "/query", json={"domanda": domanda})
            if r:
                d = r.json()
                st.markdown(d.get("risposta",""))
                if d.get("fonti"): st.caption("Fonti: " + " · ".join(d["fonti"]))
                st.session_state.messages.append({"role":"assistant","content":d.get("risposta",""),"fonti":d.get("fonti",[])})

elif page == "📄 Documenti":
    st.title("📄 Documenti")
    t1, t2 = st.tabs(["📁 Documenti Progetto","📜 Contratto"])
    with t1:
        f = st.file_uploader("Carica documento", type=["pdf","docx","msg","pptx","txt"])
        if f and st.button("📤 Processa"):
            with st.spinner(f"Elaborazione {f.name}..."):
                r = api("post", "/ingest", files={"file":(f.name, f.getvalue(), f.type)})
            if r:
                res = r.json()
                st.success(f"✅ {res.get('chunks',0)} chunk indicizzati")
                c = st.columns(4)
                c[0].metric("Chunk", res.get("chunks",0))
                c[1].metric("Indicizzati", res.get("indexing",{}).get("chunks_indexed","?"))
                c[2].metric("Decisioni", res.get("decisions",{}).get("found",0))
                sc = res.get("scope",{}); c[3].metric("Scope", "⚠️ Fuori" if sc.get("fuori_scope") else "✅ OK")
    with t2:
        f = st.file_uploader("Carica contratto", type=["pdf","docx","txt"], key="contract")
        if f and st.button("📜 Carica"):
            with st.spinner("Indicizzazione..."):
                r = api("post", "/load-contract", files={"file":(f.name, f.getvalue(), f.type)})
            if r: st.success(r.json().get("message",""))

elif page == "🛡 Scope & CR":
    st.title("🛡 Scope Check & Change Request")
    t1, t2 = st.tabs(["🔍 Verifica","📋 Storico Alert"])
    with t1:
        comunicazione = st.text_area("Richiesta da verificare", height=150)
        if st.button("🔍 Verifica Scope") and comunicazione:
            with st.spinner("Analisi..."):
                r = api("post", "/scope-check", json={"comunicazione": comunicazione})
            if r:
                res = r.json()
                if res.get("warning"): st.warning(res["warning"])
                elif res.get("fuori_scope"):
                    st.error("⚠️ FUORI SCOPE")
                    for e in res.get("elementi",[]): st.markdown(f"- {e}")
                    st.markdown(f"**Motivazione:** {res.get('motivazione','')}")
                else:
                    st.success("✅ Nel perimetro contrattuale")
    with t2:
        show_all = st.toggle("Mostra anche gestiti")
        r = api("get", f"/alerts?unhandled_only={not show_all}")
        if r:
            for a in r.json():
                st.info if a["is_handled"] else st.warning
                with st.expander(f"{'✅' if a['is_handled'] else '⚠️'} [{a['created_at'][:10]}] {a['source']}"):
                    st.markdown(a["alert_text"])
                    if a.get("cr_draft"):
                        st.text(a["cr_draft"])
                    if not a["is_handled"]:
                        if st.button("✅ Gestito", key=f"hh{a['id']}"):
                            api("post", f"/alerts/{a['id']}/handle"); st.rerun()

elif page == "📘 Playbook":
    st.title("📘 Project Playbook")
    st.warning("Legge tutta la memoria del progetto — può richiedere 1-2 minuti.")
    if st.button("🚀 Genera Playbook"):
        with st.spinner("Generazione..."):
            r = api("post", "/playbook")
        if r:
            st.success("✅ Pronto!")
            st.download_button("📥 Scarica playbook.docx", r.content, "playbook.docx",
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
ENDAPP

# ── setup_deployments.py ──────────────────────────────────────────────────────
cat > setup_deployments.py << 'ENDSETUP'
import os, json, time, requests
from dotenv import load_dotenv
load_dotenv()

AUTH_URL = os.getenv("AICORE_AUTH_URL","").rstrip("/") + "/oauth/token"
BASE_URL = os.getenv("AICORE_BASE_URL","").rstrip("/")
RG = os.getenv("AICORE_RESOURCE_GROUP","i-pmp")
CHAT_MODEL = os.getenv("CHAT_MODEL","anthropic--claude-4.6-sonnet")
EMBED_MODEL = os.getenv("EMBED_MODEL","text-embedding-3-large")

def get_token():
    r = requests.post(AUTH_URL, data={"grant_type":"client_credentials"},
                      auth=(os.getenv("AICORE_CLIENT_ID"), os.getenv("AICORE_CLIENT_SECRET")), timeout=15)
    r.raise_for_status(); return r.json()["access_token"]

def hdrs(token):
    return {"Authorization":f"Bearer {token}","AI-Resource-Group":RG,"Content-Type":"application/json"}

def find_or_create(token, model_name):
    r = requests.get(f"{BASE_URL}/v2/lm/deployments", headers=hdrs(token), timeout=15)
    r.raise_for_status()
    for d in r.json().get("resources",[]):
        m = (d.get("details",{}) or {}).get("resources",{}).get("backendDetails",{}).get("model",{}).get("name","")
        if m == model_name and d.get("status") == "RUNNING":
            print(f"  Trovato RUNNING: {d['id']}"); return d["id"]
    print(f"  Creo configurazione per {model_name}...")
    cfg = requests.post(f"{BASE_URL}/v2/lm/configurations", headers=hdrs(token),
                        json={"name":f"ipmp-{model_name.replace('--','-')}",
                              "executableId":model_name,"scenarioId":"foundation-models",
                              "versionId":"0.0.1","parameterBindings":[],"inputArtifactBindings":[]}, timeout=15)
    if not cfg.ok:
        print(f"  Config fallita: {cfg.text}"); return ""
    config_id = cfg.json()["id"]
    dep = requests.post(f"{BASE_URL}/v2/lm/deployments", headers=hdrs(token),
                        json={"configurationId":config_id}, timeout=15)
    if not dep.ok:
        print(f"  Deploy fallito: {dep.text}"); return ""
    dep_id = dep.json()["id"]
    print(f"  Deployment avviato: {dep_id} — attendo RUNNING...")
    for _ in range(30):
        time.sleep(10)
        token = get_token()
        st = requests.get(f"{BASE_URL}/v2/lm/deployments/{dep_id}", headers=hdrs(token), timeout=15).json().get("status","")
        print(f"    {st}")
        if st == "RUNNING": return dep_id
        if st in ("DEAD","STOPPED","ERROR"): return ""
    return dep_id

print("=== i-PMP Setup Deployments ===")
token = get_token(); print("Token OK")
print(f"\nChat: {CHAT_MODEL}"); chat_id = find_or_create(token, CHAT_MODEL)
print(f"\nEmbed: {EMBED_MODEL}"); token = get_token(); embed_id = find_or_create(token, EMBED_MODEL)
print(f"\n=== COPIA NEL .env ===")
print(f"CHAT_DEPLOYMENT_ID={chat_id}")
print(f"EMBED_DEPLOYMENT_ID={embed_id}")
ENDSETUP

# ── start.sh ──────────────────────────────────────────────────────────────────
cat > start.sh << 'ENDSTART'
#!/bin/bash
set -e
cd "$(dirname "$0")"
[ ! -f .env ] && echo "❌ .env mancante" && exit 1
export $(grep -v '^#' .env | grep -v '^$' | xargs)
if [ "$CHAT_DEPLOYMENT_ID" = "<da-compilare>" ] || [ -z "$CHAT_DEPLOYMENT_ID" ]; then
  echo "⚠️  CHAT_DEPLOYMENT_ID non configurato. Esegui: python setup_deployments.py"
  exit 1
fi
echo "🚀 Avvio backend (porta 8000)..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning &
BACKEND_PID=$!
for i in $(seq 1 20); do
  curl -sf http://localhost:8000/health > /dev/null 2>&1 && echo "✅ Backend pronto" && break
  sleep 1
done
echo "🎨 Avvio frontend (porta 8501)..."
BACKEND_URL=http://localhost:8000 streamlit run app/app.py \
  --server.port 8501 --server.headless true --server.address 0.0.0.0 \
  --browser.gatherUsageStats false &
FRONTEND_PID=$!
echo ""
echo "✅ i-PMP in esecuzione"
echo "   Frontend → http://localhost:8501"
echo "   API docs → http://localhost:8000/docs"
echo "   Ctrl+C per fermare"
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait $BACKEND_PID $FRONTEND_PID
ENDSTART

chmod +x start.sh

echo ""
echo "✅ Progetto i-PMP creato in ~/projects/ipmp"
echo ""
echo "📋 Prossimi step:"
echo "   1. pip install -r requirements.txt"
echo "   2. python setup_deployments.py   # ottieni i deployment ID"
echo "   3. Aggiorna .env con i deployment ID"
echo "   4. Crea le tabelle HANA (copia sql/schema.sql in HANA DB Explorer)"
echo "   5. ./start.sh"
