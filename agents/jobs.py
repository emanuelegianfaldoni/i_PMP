"""
Coda delle elaborazioni documenti in background.

Il frontend carica il file e riceve subito un job; il backend lo elabora in un thread
dedicato (un documento alla volta, per non moltiplicare carico su AI Core e memoria)
e il frontend ne legge l'avanzamento con GET /jobs.

Lo stato è in memoria: un riavvio del backend interrompe le elaborazioni e ne perde lo stato.
"""
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from agents.extraction_agent import (
    pipeline_ingestion, rimuovi_documento, DocumentoTroppoGrande, Annullato,
)

ATTIVI = ("in_coda", "in_corso")
_MAX_STORICO = 100

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


class Job:
    def __init__(self, project_id: str, source: str, lotto: str = None):
        self.id = uuid.uuid4().hex
        self.project_id = project_id
        self.source = source
        self.lotto = lotto  # identifica i file inviati insieme, per il riepilogo nel frontend
        self.stato = "in_coda"
        self.fase = "In attesa"
        self.chunks_totali = 0
        self.indicizzati = 0
        self.analizzati = 0
        self.risultato = None
        self.errore = None
        self.creato_il = time.time()
        self.finito_il = None
        self.ha_scritto = False  # True da quando la pipeline inizia a modificare il database
        self._annulla = threading.Event()
        self._lock = threading.Lock()

    @property
    def annullato(self) -> bool:
        return self._annulla.is_set()

    def avanza(self, indicizzati: int = 0, analizzati: int = 0) -> None:
        with self._lock:
            self.indicizzati += indicizzati
            self.analizzati += analizzati

    def percentuale(self) -> float:
        if self.stato not in ATTIVI:
            return 1.0
        if not self.chunks_totali:
            return 0.0
        # Indicizzazione (embedding) e analisi decisioni procedono in parallelo: pesano metà ciascuna
        fatto = min(self.indicizzati, self.chunks_totali) + min(self.analizzati, self.chunks_totali)
        return fatto / (2 * self.chunks_totali)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "source": self.source,
            "lotto": self.lotto,
            "stato": self.stato,
            "fase": self.fase,
            "chunks_totali": self.chunks_totali,
            "percentuale": round(self.percentuale(), 3),
            "risultato": self.risultato,
            "errore": self.errore,
            "creato_il": self.creato_il,
            "finito_il": self.finito_il,
        }


def avvia(tmp_path: str, project_id: str, source: str, lotto: str = None) -> Job:
    job = Job(project_id, source, lotto)
    with _lock:
        _jobs[job.id] = job
        _pulisci_storico()
    _executor.submit(_esegui, job, tmp_path)
    return job


def annulla(job_id: str) -> Job | None:
    job = _jobs.get(job_id)
    if job and job.stato in ATTIVI:
        job._annulla.set()
        if job.stato == "in_coda":
            job.fase = "Annullato prima dell'avvio"
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def elenco(project_id: str) -> list[dict]:
    with _lock:
        jobs = [j for j in _jobs.values() if j.project_id == project_id]
    return [j.to_dict() for j in sorted(jobs, key=lambda j: j.creato_il)]


def _esegui(job: Job, tmp_path: str) -> None:
    try:
        if job.annullato:
            raise Annullato()
        job.stato = "in_corso"
        job.fase = "Lettura del file"
        job.risultato = pipeline_ingestion(tmp_path, job.project_id, source_name=job.source, job=job)
        job.stato = "completato"
        job.fase = "Completato"
    except Annullato:
        # Elimina quanto già salvato, così non resta un documento a metà
        if job.ha_scritto:
            try:
                rimuovi_documento(job.project_id, job.source)
            except Exception as e:
                print(f"[jobs] Pulizia dopo annullamento fallita per {job.source}: {e}", file=sys.stderr)
        job.stato = "annullato"
        job.fase = "Annullato"
    except DocumentoTroppoGrande as e:
        job.stato = "errore"
        job.fase = "Rifiutato"
        job.errore = str(e)
    except Exception as e:
        print(f"[jobs] Errore su {job.source}: {e}", file=sys.stderr)
        job.stato = "errore"
        job.fase = "Errore"
        job.errore = str(e)
    finally:
        job.finito_il = time.time()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _pulisci_storico() -> None:
    finiti = sorted((j for j in _jobs.values() if j.stato not in ATTIVI), key=lambda j: j.creato_il)
    for j in finiti[:max(0, len(finiti) - _MAX_STORICO)]:
        del _jobs[j.id]
