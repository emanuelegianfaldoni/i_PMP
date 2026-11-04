import os
import sys
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from agents.extraction_agent import pipeline_ingestion, DocumentoTroppoGrande
from agents import jobs
from agents.orchestrator import Orchestrator
from agents.scope_guardian import ScopeGuardian, carica_contratto
from agents.playbook import PlaybookAgent

app = FastAPI(title="i-PMP API", version="1.0")

# Il frontend invia i documenti direttamente dal browser (POST /ingest-jobs): serve il CORS per il suo dominio
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("FRONTEND_URL", "http://localhost:8501").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PROJECT_ID = os.getenv("PROJECT_ID", "demo")
_orchestrator = Orchestrator()
_scope = ScopeGuardian()


@app.on_event("startup")
def startup():
    from services.hana import get_connection, migrate
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
    disk_free_mb = shutil.disk_usage(tempfile.gettempdir()).free // (1024 * 1024)
    return {"status": "ok", "project_id": PROJECT_ID, "disk_free_mb": disk_free_mb}


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
    except DocumentoTroppoGrande as e:
        raise HTTPException(status_code=413, detail=str(e))
    finally:
        os.unlink(tmp_path)
    return result


@app.post("/ingest-job", status_code=202)
async def ingest_job(file: UploadFile = File(...), project_id: str = Query(None)):
    """Accoda il documento e risponde subito: l'avanzamento si legge con GET /jobs."""
    pid = project_id or PROJECT_ID
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        while chunk := await file.read(8 * 1024 * 1024):
            tmp.write(chunk)
        tmp_path = tmp.name
    return jobs.avvia(tmp_path, pid, file.filename).to_dict()


@app.post("/ingest-jobs", status_code=202)
async def ingest_jobs(files: list[UploadFile] = File(...), project_id: str = Query(None),
                      lotto: str = Query(None)):
    """Riceve più documenti in una richiesta (invio diretto dal browser) e li accoda tutti insieme.

    Le elaborazioni partono solo quando l'invio è completo: il frontend si aggiorna a quel punto,
    senza interrompere un invio in corso.
    """
    pid = project_id or PROJECT_ID
    salvati = []
    try:
        for f in files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.filename).suffix) as tmp:
                while chunk := await f.read(8 * 1024 * 1024):
                    tmp.write(chunk)
            await f.close()   # libera subito la copia temporanea del multipart
            salvati.append((tmp.name, f.filename))
    except Exception:
        for path, _ in salvati:
            os.unlink(path)
        raise
    return [jobs.avvia(path, pid, nome, lotto).to_dict() for path, nome in salvati]


@app.get("/jobs")
def get_jobs(project_id: str = Query(None)):
    return jobs.elenco(project_id or PROJECT_ID)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Elaborazione non trovata (il server potrebbe essere stato riavviato)")
    return job.to_dict()


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.annulla(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Elaborazione non trovata")
    return job.to_dict()


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


@app.get("/documents")
def get_documents(project_id: str = Query(None)):
    """Elenco dei documenti in memoria con chunk, decisioni estratte e alert aperti."""
    from services.hana import get_connection
    pid = project_id or PROJECT_ID
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT SOURCE_NAME, COUNT(*), MAX(CREATED_AT) FROM IPMP_DOCS "
            "WHERE PROJECT_ID=? GROUP BY SOURCE_NAME", [pid])
        docs = cursor.fetchall()
        cursor.execute(
            "SELECT SOURCE_NAME, COUNT(*) FROM IPMP_DECISIONS WHERE PROJECT_ID=? GROUP BY SOURCE_NAME", [pid])
        decisioni = dict(cursor.fetchall())
        cursor.execute(
            "SELECT SOURCE_NAME, COUNT(*) FROM IPMP_ALERTS WHERE PROJECT_ID=? AND IS_HANDLED=0 "
            "GROUP BY SOURCE_NAME", [pid])
        alert = dict(cursor.fetchall())
    finally:
        cursor.close()
        conn.close()
    return sorted(
        [
            {
                "source": r[0],
                "chunks": r[1],
                "caricato_il": str(r[2])[:16],
                "decisioni": decisioni.get(r[0], 0),
                "alert_aperti": alert.get(r[0], 0),
                "contratto": (r[0] or "").startswith("CONTRATTO::"),
            }
            for r in docs
        ],
        key=lambda d: d["caricato_il"],
        reverse=True,
    )


@app.delete("/documents")
def delete_document(source: str = Query(...), project_id: str = Query(None)):
    """Elimina un singolo documento: chunk, riassunto, decisioni e alert non gestiti."""
    from agents.extraction_agent import rimuovi_documento
    pid = project_id or PROJECT_ID
    deleted = rimuovi_documento(pid, source)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Documento non trovato: {source}")
    return {"project_id": pid, "source": source, "deleted": deleted}


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
