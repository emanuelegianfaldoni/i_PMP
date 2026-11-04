import os
import sys
import json
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from services.hana import get_connection, is_sqlite


def itera_testo(file_path: str):
    """Testo del file a pezzi (righe, pagine, email...), senza caricarlo tutto in memoria."""
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                yield page.extract_text() or ""
                page.close()   # libera la cache della pagina
        return

    if ext == ".docx":
        from docx import Document
        doc = Document(file_path)
        for p in doc.paragraphs:
            if p.text.strip():
                yield p.text
        return

    if ext == ".msg":
        import extract_msg
        msg = extract_msg.Message(file_path)
        for part in (msg.subject or "", msg.body or ""):
            if part:
                yield part
        return

    if ext == ".pst":
        yield from _itera_pst(file_path)
        return

    if ext in (".xlsx", ".xls"):
        import openpyxl
        _controlla_dimensione_xlsx(file_path)
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        try:
            for sheet in wb.worksheets:
                yield f"[Foglio: {sheet.title}]"
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        yield "\t".join(cells)
        finally:
            wb.close()
        return

    if ext == ".csv":
        import csv
        # Gli export di Excel in italiano usano ";" e spesso codifica cp1252
        encoding = _codifica_testo(file_path)
        with open(file_path, encoding=encoding, errors="replace", newline="") as f:
            campione = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(campione, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            for row in csv.reader(f, dialect):
                cells = [c.strip() for c in row if c.strip()]
                if cells:
                    yield "\t".join(cells)
        return

    if ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(file_path)
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        if para.text.strip():
                            yield para.text
        return

    # .txt, .md, e qualsiasi altro formato testuale
    with open(file_path, encoding="utf-8", errors="replace") as f:
        for riga in f:
            yield riga


def _codifica_testo(file_path: str) -> str:
    """utf-8 se l'intero file è utf-8 valido, altrimenti cp1252 (export Excel italiani). Legge a blocchi."""
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with open(file_path, "rb") as f:
            while blocco := f.read(8 * 1024 * 1024):
                decoder.decode(blocco)
            decoder.decode(b"", final=True)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


# Oltre questa dimensione il dizionario dei testi di un .xlsx (caricato tutto in memoria da openpyxl)
# può esaurire la memoria del backend; e un documento così supera comunque il limite di chunk
_MAX_SHARED_STRINGS_MB = 40


def _controlla_dimensione_xlsx(file_path: str) -> None:
    import zipfile
    try:
        with zipfile.ZipFile(file_path) as z:
            mb = z.getinfo("xl/sharedStrings.xml").file_size / (1024 * 1024)
    except (KeyError, zipfile.BadZipFile):
        return
    if mb > _MAX_SHARED_STRINGS_MB:
        raise DocumentoTroppoGrande(
            f"file Excel troppo grande ({mb:.0f} MB di testo). "
            "Caricane solo la parte rilevante oppure dividilo in più file.")


def _itera_pst(file_path: str):
    import pypff
    pst_file = pypff.file()
    pst_file.open(file_path)
    gia_visti = set()

    def _decode(raw) -> str:
        if raw is None:
            return ""
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    def _strip_html(html: str) -> str:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)

    def _email(subject: str, sender: str, body: str) -> str:
        header = f"--- Email: {subject}"
        if sender:
            header += f" (Da: {sender})"
        return f"{header} ---\n{body.strip()}"

    def _estrai_cartella(folder, livello=0):
        nome = ""
        try:
            nome = _decode(folder.name) or "(root)"
        except Exception:
            nome = "(root)"
        n_msg = folder.number_of_sub_messages
        n_sub = folder.number_of_sub_folders
        n_items = folder.number_of_sub_items
        print(f"[PST] {'  '*livello}📁 {nome}: {n_msg} email, {n_items} items totali, {n_sub} sottocartelle", file=sys.stderr)

        for i in range(n_msg):
            try:
                msg = folder.get_sub_message(i)
                subject = _decode(msg.subject)
                sender = _decode(msg.sender_name)
                body = _decode(msg.plain_text_body)
                if not body.strip():
                    body = _strip_html(_decode(msg.html_body))
                print(f"[PST] {'  '*livello}  ✉ [{i}] {subject!r} plain={len(_decode(msg.plain_text_body))}c html={len(_decode(msg.html_body))}c", file=sys.stderr)
                if subject or body.strip():
                    testo = _email(subject, sender, body)
                    gia_visti.add(hashlib.sha256(testo.encode()).digest())
                    yield testo
            except Exception as e:
                print(f"[PST] {'  '*livello}  ⚠ [{i}] errore: {e}", file=sys.stderr)

        # Prova anche gli item generici se ci sono più items che messaggi
        if n_items > n_msg:
            print(f"[PST] {'  '*livello}  → {n_items - n_msg} item non-messaggio trovati, provo sub_items...", file=sys.stderr)
            for i in range(n_items):
                try:
                    item = folder.get_sub_item(i)
                    subject = ""
                    sender = ""
                    body = ""
                    try:
                        subject = _decode(item.subject)
                    except Exception:
                        pass
                    try:
                        sender = _decode(item.sender_name)
                    except Exception:
                        pass
                    try:
                        body = _decode(item.plain_text_body)
                        if not body.strip():
                            body = _strip_html(_decode(item.html_body))
                    except Exception:
                        pass
                    if subject or body.strip():
                        print(f"[PST] {'  '*livello}  📎 item[{i}] {subject!r}", file=sys.stderr)
                        testo = _email(subject, sender, body)
                        h = hashlib.sha256(testo.encode()).digest()
                        if h not in gia_visti:
                            gia_visti.add(h)
                            yield testo
                except Exception as e:
                    print(f"[PST] {'  '*livello}  ⚠ item[{i}] errore: {e}", file=sys.stderr)

        for i in range(n_sub):
            try:
                yield from _estrai_cartella(folder.get_sub_folder(i), livello + 1)
            except Exception as e:
                print(f"[PST] {'  '*livello}  ⚠ sottocartella [{i}] errore: {e}", file=sys.stderr)

    try:
        yield from _estrai_cartella(pst_file.get_root_folder())
    finally:
        pst_file.close()


def leggi_file(file_path: str) -> str:
    return "\n".join(itera_testo(file_path))


def spezza(text: str, dim: int = 100, overlap: int = 15) -> list[str]:
    words = text.split()
    stride = max(dim - overlap, 1)
    chunks = []
    for start in range(0, len(words), stride):
        chunk = " ".join(words[start:start + dim])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


_TESTO_INIZIALE = 20_000   # caratteri tenuti per riassunto e Scope Guardian (ne usano meno di 10.000)


def estrai(file_path: str, max_chunks: int, dim: int = 100, overlap: int = 15):
    """Legge il file a flusso e restituisce (testo iniziale, chunk, impronta).

    Chunk e impronta sono identici a spezza(leggi_file()) e impronta(leggi_file()), ma la memoria usata
    resta limitata; oltre max_chunks si ferma subito con DocumentoTroppoGrande.
    """
    from collections import deque
    stride = max(dim - overlap, 1)
    buffer, chunks = deque(), []
    h = hashlib.sha256()
    prima_parola = True
    iniziale, n_iniziale = [], 0

    def emetti():
        chunks.append(" ".join(list(buffer)[:dim]))
        if len(chunks) > max_chunks:
            raise DocumentoTroppoGrande(
                f"documento troppo grande (oltre {max_chunks} chunk). "
                "Caricane solo la parte rilevante oppure dividilo in più file.")

    testo = itera_testo(file_path)
    try:
        for pezzo in testo:
            if n_iniziale < _TESTO_INIZIALE:
                iniziale.append(pezzo)
                n_iniziale += len(pezzo) + 1
            for parola in pezzo.split():
                h.update(((" " if not prima_parola else "") + parola).encode("utf-8"))
                prima_parola = False
                buffer.append(parola)
                if len(buffer) == dim:
                    emetti()
                    for _ in range(stride):
                        buffer.popleft()
    finally:
        testo.close()   # chiude il file anche se ci si ferma prima della fine
    while buffer:
        emetti()
        if len(buffer) <= stride:
            break
        for _ in range(stride):
            buffer.popleft()
    return "\n".join(iniziale)[:_TESTO_INIZIALE], chunks, h.hexdigest()


MAX_CHUNKS = int(os.getenv("MAX_CHUNKS_DOCUMENTO", "5000"))

# File di dati tabellari: non contengono decisioni di progetto, l'analisi con il modello sarebbe solo un costo
ESTENSIONI_TABELLARI = (".csv", ".xlsx", ".xls")


class DocumentoTroppoGrande(ValueError):
    pass


class Annullato(Exception):
    pass


def impronta(text: str) -> str:
    """SHA-256 del testo con spazi normalizzati: stesso contenuto = stessa impronta."""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def trova_duplicato(project_id: str, content_hash: str):
    """Nome del documento già caricato con lo stesso contenuto, oppure None."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT SOURCE_NAME FROM IPMP_DOCS WHERE PROJECT_ID=? AND CONTENT_HASH=? LIMIT 1",
            [project_id, content_hash],
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()


def rimuovi_documento(project_id: str, source_name: str) -> int:
    """Elimina la versione precedente di un documento. Gli alert già gestiti restano come storico."""
    conn = get_connection()
    cursor = conn.cursor()
    deleted = 0
    try:
        for table in ("IPMP_DOCS", "IPMP_CONTEXT", "IPMP_DECISIONS"):
            cursor.execute(f"DELETE FROM {table} WHERE PROJECT_ID=? AND SOURCE_NAME=?",
                           [project_id, source_name])
            deleted += max(cursor.rowcount, 0)
        cursor.execute("DELETE FROM IPMP_ALERTS WHERE PROJECT_ID=? AND SOURCE_NAME=? AND IS_HANDLED=0",
                       [project_id, source_name])
        deleted += max(cursor.rowcount, 0)
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    return deleted


def pipeline_ingestion(file_path: str, project_id: str = None, source_name: str = None, job=None) -> dict:
    """job (facoltativo): riceve l'avanzamento e può chiedere l'annullamento (vedi agents/jobs.py)."""
    from agents.indexing import IndexingAgent
    from agents.context_agent import ContextAgent
    from agents.decision_tracker import DecisionTrackerAgent
    from agents.scope_guardian import ScopeGuardian

    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")

    source_name = source_name or Path(file_path).name
    # Lettura a flusso: ogni chunk costa una chiamata di embedding e i documenti enormi (es. estrazioni ALV)
    # vanno fermati prima di esaurire la memoria del backend
    try:
        full_text, chunks, content_hash = estrai(file_path, MAX_CHUNKS)
    except DocumentoTroppoGrande as e:
        raise DocumentoTroppoGrande(f"{source_name}: {e}") from None
    if job:
        job.chunks_totali = len(chunks)

    # Stesso contenuto già caricato (anche con un altro nome): non rielaborare
    duplicato = trova_duplicato(project_id, content_hash) if chunks else None
    if duplicato:
        return {"source": source_name, "chunks": 0, "duplicate_of": duplicato}

    # Stesso nome ma contenuto diverso: è una nuova versione, sostituisce la precedente
    if job:
        if job.annullato:
            raise Annullato()
        job.ha_scritto = True
    replaced = rimuovi_documento(project_id, source_name) > 0
    if job:
        job.fase = "Indicizzazione e analisi"

    results: dict = {"source": source_name, "chunks": len(chunks), "replaced": replaced}

    def run_indexing():
        try:
            n = IndexingAgent().indicizza(chunks, source_name, project_id, content_hash, job=job)
            return ("indexing", {"chunks_indexed": n})
        except Exception as e:
            return ("indexing", {"error": str(e)})

    def run_context():
        try:
            ok = ContextAgent().process(full_text, source_name, project_id)
            return ("context", {"ok": ok})
        except Exception as e:
            return ("context", {"error": str(e)})

    tabellare = Path(file_path).suffix.lower() in ESTENSIONI_TABELLARI
    if tabellare and job:
        job.avanza(analizzati=len(chunks))   # fase non eseguita: l'avanzamento segue solo l'indicizzazione

    def run_decisions():
        if tabellare:
            return ("decisions", {"found": 0, "skipped": "file di dati tabellari"})
        try:
            n = DecisionTrackerAgent().process(chunks, source_name, project_id, job=job)
            return ("decisions", {"found": n})
        except Exception as e:
            return ("decisions", {"error": str(e)})

    def run_scope():
        try:
            r = ScopeGuardian().check(full_text, source_name, project_id)
            return ("scope", r)
        except Exception as e:
            return ("scope", {"error": str(e)})

    with ThreadPoolExecutor(max_workers=1 if is_sqlite() else 4) as pool:
        futures = [
            pool.submit(run_indexing),
            pool.submit(run_context),
            pool.submit(run_decisions),
            pool.submit(run_scope),
        ]
        for f in as_completed(futures):
            key, val = f.result()
            results[key] = val

    if job and job.annullato:
        raise Annullato()
    return results


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if len(sys.argv) < 2:
        print("Uso: python -m agents.extraction_agent <file>")
        sys.exit(1)
    result = pipeline_ingestion(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
