import os
import sys
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from agents.extraction_agent import pipeline_ingestion
from agents.orchestrator import Orchestrator
from agents.scope_guardian import ScopeGuardian, carica_contratto
from agents.playbook import PlaybookAgent

app = FastAPI(title="i-PMP API", version="1.0")

PROJECT_ID = os.getenv("PROJECT_ID", "demo")
_orchestrator = Orchestrator()
_scope = ScopeGuardian()


@app.on_event("startup")
def startup():
    from services.hana import get_connection, migrate, is_sqlite
    if is_sqlite():
        conn = get_connection()
        migrate(conn)
        conn.close()


class QueryRequest(BaseModel):
    domanda: str
    project_id: str = None
    modalita: str = "standard"


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
        # Streaming write per file grandi
        while chunk := await file.read(8 * 1024 * 1024):  # 8MB chunks
            tmp.write(chunk)
        tmp_path = tmp.name
    try:
        result = pipeline_ingestion(tmp_path, pid, source_name=file.filename)
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/load-contract")
async def load_contract(file: UploadFile = File(...), project_id: str = Query(None)):
    pid = project_id or PROJECT_ID
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        n = carica_contratto(tmp_path, pid, original_name=file.filename)
    finally:
        os.unlink(tmp_path)
    return {"chunks_loaded": n, "message": f"Contratto caricato: {n} chunk indicizzati"}


@app.post("/query")
def query(body: QueryRequest):
    pid = body.project_id or PROJECT_ID
    try:
        result = _orchestrator.smista(body.domanda, pid, modalita=body.modalita)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@app.post("/scope-check")
def scope_check(body: ScopeRequest):
    pid = body.project_id or PROJECT_ID
    try:
        result = _scope.check(body.comunicazione, "on-demand", pid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@app.get("/alerts")
def get_alerts(project_id: str = Query(None), unhandled_only: bool = True):
    pid = project_id or PROJECT_ID
    return _scope.get_alerts(pid, unhandled_only)


@app.post("/alerts/{alert_id}/handle")
def handle_alert(alert_id: int):
    _scope.mark_handled(alert_id)
    return {"ok": True}


@app.get("/decisions")
def get_decisions(project_id: str = Query(None)):
    from services.hana import get_connection
    pid = project_id or PROJECT_ID
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT ID, DESCRIZIONE, AUTORE, DATA_DECISIONE, MOTIVAZIONE, SOURCE_NAME, CREATED_AT "
            "FROM IPMP_DECISIONS WHERE PROJECT_ID=? ORDER BY CREATED_AT DESC",
            [pid],
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()
    return [
        {
            "id": r[0],
            "descrizione": r[1],
            "autore": r[2],
            "data": str(r[3]),
            "motivazione": r[4],
            "source": r[5],
            "created_at": str(r[6]),
        }
        for r in rows
    ]


@app.post("/change-request")
async def load_change_request(file: UploadFile = File(...), project_id: str = Query(None)):
    """Carica una Change Request approvata nel baseline contrattuale (CONTRATTO:: chunks)."""
    pid = project_id or PROJECT_ID
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        while chunk := await file.read(8 * 1024 * 1024):
            tmp.write(chunk)
        tmp_path = tmp.name
    try:
        n = carica_contratto(tmp_path, pid, original_name=f"CR::{file.filename}")
    finally:
        os.unlink(tmp_path)
    return {"chunks_loaded": n, "message": f"Change Request caricata nel baseline: {n} chunk indicizzati"}


@app.delete("/contract")
def reset_contract(project_id: str = Query(None)):
    from services.hana import get_connection
    pid = project_id or PROJECT_ID
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM IPMP_DOCS WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%'", [pid])
        n = cursor.fetchone()[0]
        cursor.execute("DELETE FROM IPMP_DOCS WHERE PROJECT_ID=? AND SOURCE_NAME LIKE 'CONTRATTO::%'", [pid])
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return {"project_id": pid, "deleted_chunks": n}


@app.delete("/memory")
def reset_memory(project_id: str = Query(None), target: str = Query("documenti", description="'documenti' = solo docs+context, 'tutto' = anche decisioni e alert")):
    from services.hana import get_connection
    pid = project_id or PROJECT_ID
    if target == "tutto":
        tables = ("IPMP_DOCS", "IPMP_CONTEXT", "IPMP_DECISIONS", "IPMP_ALERTS")
    else:
        tables = ("IPMP_DOCS", "IPMP_CONTEXT")
    conn = get_connection()
    cursor = conn.cursor()
    totals = {}
    try:
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE PROJECT_ID=?", [pid])
            totals[table] = cursor.fetchone()[0]
            cursor.execute(f"DELETE FROM {table} WHERE PROJECT_ID=?", [pid])
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return {"project_id": pid, "target": target, "deleted": totals}


@app.post("/playbook")
def playbook(project_id: str = Query(None)):
    pid = project_id or PROJECT_ID
    output_path = f"/tmp/playbook_{pid}.docx"
    try:
        path = PlaybookAgent().genera(pid, output_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return FileResponse(path, filename="playbook.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
