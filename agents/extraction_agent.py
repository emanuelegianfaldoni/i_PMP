import os
import sys
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from services.hana import is_sqlite

SUPPORTED_EXT = {".pdf", ".docx", ".msg", ".pst", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md"}
TABULAR_EXT = {".xlsx", ".xls", ".csv"}
MAX_ARCHIVE_BYTES = 5 * 1024 ** 3  # difesa da zip bomb: 5 GB decompressi


def _cella(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _tabella_in_righe(rows, titolo: str) -> list[str]:
    """Ogni riga diventa 'Colonna: valore | ...' così resta comprensibile anche presa da sola."""
    out = [f"[{titolo}]"]
    header = None
    for row in rows:
        cells = [_cella(c) for c in row]
        if not any(cells):
            continue
        if header is None:
            header = [c or f"Colonna {i + 1}" for i, c in enumerate(cells)]
            continue
        pairs = []
        for i, val in enumerate(cells):
            if val:
                name = header[i] if i < len(header) else f"Colonna {i + 1}"
                pairs.append(f"{name}: {val}")
        out.append(" | ".join(pairs))
    if header is not None and len(out) == 1:
        out.append(" | ".join(header))
    return out


def spezza_righe(text: str, max_words: int = 120) -> list[str]:
    """Chunking per tabelle: non spezza mai una riga e ripete il titolo del foglio in ogni chunk."""
    chunks, current, n_words, titolo = [], [], 0, ""

    def flush():
        nonlocal current, n_words
        if current:
            chunks.append("\n".join(([titolo] if titolo else []) + current))
        current, n_words = [], 0

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            titolo = line
            continue
        w = len(line.split())
        if w > max_words:
            flush()
            for piece in spezza(line, dim=max_words, overlap=max_words // 8):
                chunks.append(f"{titolo}\n{piece}" if titolo else piece)
            continue
        if n_words + w > max_words:
            flush()
        current.append(line)
        n_words += w
    flush()
    return chunks


def estrai_archivio(zip_path: str, dest_dir: str) -> list[tuple[str, str]]:
    """Estrae dallo ZIP solo i file supportati. Ritorna (percorso_su_disco, percorso_relativo)."""
    import zipfile
    dest = Path(dest_dir).resolve()
    estratti = []
    with zipfile.ZipFile(zip_path) as zf:
        membri = [
            m for m in zf.infolist()
            if not m.is_dir()
            and Path(m.filename).suffix.lower() in SUPPORTED_EXT
            and not any(p.startswith(".") or p == "__MACOSX" for p in Path(m.filename).parts)
        ]
        if sum(m.file_size for m in membri) > MAX_ARCHIVE_BYTES:
            raise ValueError("Archivio troppo grande una volta decompresso (limite 5 GB)")
        for m in membri:
            target = (dest / m.filename).resolve()
            if dest not in target.parents:
                continue  # percorso che esce dalla cartella di destinazione (zip slip)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            estratti.append((str(target), m.filename))
    return estratti


def leggi_file(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(pages)

    if ext == ".docx":
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if ext == ".msg":
        import extract_msg
        msg = extract_msg.Message(file_path)
        parts = [msg.subject or "", msg.body or ""]
        return "\n".join(p for p in parts if p)

    if ext == ".pst":
        import pypff
        pst_file = pypff.file()
        pst_file.open(file_path)
        parts = []

        def _decode(raw) -> str:
            if raw is None:
                return ""
            return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

        def _strip_html(html: str) -> str:
            from bs4 import BeautifulSoup
            return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)

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
                        header = f"--- Email: {subject}"
                        if sender:
                            header += f" (Da: {sender})"
                        parts.append(f"{header} ---\n{body.strip()}")
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
                            header = f"--- Email: {subject}"
                            if sender:
                                header += f" (Da: {sender})"
                            if f"{header} ---\n{body.strip()}" not in parts:
                                parts.append(f"{header} ---\n{body.strip()}")
                    except Exception as e:
                        print(f"[PST] {'  '*livello}  ⚠ item[{i}] errore: {e}", file=sys.stderr)

            for i in range(n_sub):
                try:
                    _estrai_cartella(folder.get_sub_folder(i), livello + 1)
                except Exception as e:
                    print(f"[PST] {'  '*livello}  ⚠ sottocartella [{i}] errore: {e}", file=sys.stderr)

        _estrai_cartella(pst_file.get_root_folder())
        pst_file.close()
        return "\n\n".join(parts)

    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.extend(_tabella_in_righe(sheet.iter_rows(values_only=True), f"Foglio: {sheet.title}"))
        wb.close()
        return "\n".join(parts)

    if ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(file_path)
        parts = []
        for sheet in wb.sheets():
            rows = (sheet.row_values(i) for i in range(sheet.nrows))
            parts.extend(_tabella_in_righe(rows, f"Foglio: {sheet.name}"))
        return "\n".join(parts)

    if ext == ".csv":
        import csv
        raw = Path(file_path).read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        try:
            dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = csv.reader(text.splitlines(), dialect)
        return "\n".join(_tabella_in_righe(rows, f"File: {Path(file_path).name}"))

    if ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(file_path)
        texts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        texts.append(para.text)
        return "\n".join(t for t in texts if t.strip())

    # .txt, .md, e qualsiasi altro formato testuale
    return Path(file_path).read_text(encoding="utf-8", errors="replace")


def spezza(text: str, dim: int = 100, overlap: int = 15) -> list[str]:
    words = text.split()
    stride = max(dim - overlap, 1)
    chunks = []
    for start in range(0, len(words), stride):
        chunk = " ".join(words[start:start + dim])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def pipeline_ingestion(file_path: str, project_id: str = None, source_name: str = None) -> dict:
    from agents.indexing import IndexingAgent
    from agents.context_agent import ContextAgent
    from agents.decision_tracker import DecisionTrackerAgent
    from agents.scope_guardian import ScopeGuardian

    if project_id is None:
        project_id = os.getenv("PROJECT_ID", "demo")

    source_name = source_name or Path(file_path).name
    full_text = leggi_file(file_path)
    if Path(file_path).suffix.lower() in TABULAR_EXT:
        chunks = spezza_righe(full_text)
    else:
        chunks = spezza(full_text)

    results: dict = {"source": source_name, "chunks": len(chunks)}

    def run_indexing():
        try:
            n = IndexingAgent().indicizza(chunks, source_name, project_id)
            return ("indexing", {"chunks_indexed": n})
        except Exception as e:
            return ("indexing", {"error": str(e)})

    def run_context():
        try:
            ok = ContextAgent().process(full_text, source_name, project_id)
            return ("context", {"ok": ok})
        except Exception as e:
            return ("context", {"error": str(e)})

    def run_decisions():
        try:
            n = DecisionTrackerAgent().process(chunks, source_name, project_id)
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

    return results


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if len(sys.argv) < 2:
        print("Uso: python -m agents.extraction_agent <file>")
        sys.exit(1)
    result = pipeline_ingestion(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
