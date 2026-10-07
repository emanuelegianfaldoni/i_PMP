import io
import os
import sys
import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from services.hana import is_sqlite

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_EXT = {".pdf", ".docx", ".msg", ".pst", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md"} | IMAGE_EXT
TABULAR_EXT = {".xlsx", ".xls", ".csv"}
MAX_ARCHIVE_BYTES = 5 * 1024 ** 3  # difesa da zip bomb: 5 GB decompressi
PDF_MIN_IMG_PT = 60       # immagini PDF più piccole (in punti) sono icone o decorazioni
PDF_PAGINA_SCANSIONATA = 30  # sotto questi caratteri di testo la pagina è trattata come scansione


class _Img:
    """Segnaposto per un'immagine da descrivere, nella posizione in cui compare nel documento."""
    __slots__ = ("data", "contesto")

    def __init__(self, data: bytes, contesto: str = ""):
        self.data = bytes(data)
        self.contesto = contesto


def _componi(segmenti: list) -> str:
    from services.vision import descrivi_tutte
    immagini = [s for s in segmenti if isinstance(s, _Img)]
    descrizioni = iter(descrivi_tutte([(i.data, i.contesto) for i in immagini]))
    out = []
    for s in segmenti:
        if isinstance(s, _Img):
            d = next(descrizioni)
            if d:
                out.append(f"[Immagine] {d}")
        elif s and s.strip():
            out.append(s)
    return "\n".join(out)


def _formatta_grafico(titolo: str, serie: list[tuple[str, list, list]]) -> str:
    righe = []
    for nome, categorie, valori in serie:
        if categorie:
            dati = ", ".join(f"{c}: {_cella(v)}" for c, v in zip(categorie, valori))
        else:
            dati = ", ".join(_cella(v) for v in valori)
        righe.append(f"{nome or 'Serie'} → {dati}")
    return f"[Grafico{': ' + titolo if titolo else ''}] " + " | ".join(righe)


def _grafico_pptx(chart) -> str:
    titolo = ""
    if chart.has_title and chart.chart_title.has_text_frame:
        titolo = chart.chart_title.text_frame.text.strip()
    serie = []
    for plot in chart.plots:
        categorie = [str(c) for c in plot.categories]
        for s in plot.series:
            serie.append((s.name, categorie, list(s.values)))
    return _formatta_grafico(titolo, serie)


def _grafici_ooxml(file_path: str, cartella: str) -> list[str]:
    """Grafici nativi di Word/Excel: i dati sono nell'XML del grafico, non serve l'AI."""
    import re
    import zipfile
    from lxml import etree
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    out = []
    with zipfile.ZipFile(file_path) as zf:
        for name in sorted(n for n in zf.namelist() if re.fullmatch(rf"{cartella}chart\d+\.xml", n)):
            try:
                root = etree.fromstring(zf.read(name), parser)
                t = root.find(f".//{C}title")
                titolo = "".join(x.text or "" for x in t.iter(f"{A}t")) if t is not None else ""
                serie = []
                for ser in root.iter(f"{C}ser"):
                    tx = ser.find(f"{C}tx")
                    nome = "".join(v.text or "" for v in tx.iter(f"{C}v")) if tx is not None else ""
                    cat = ser.find(f"{C}cat")
                    if cat is None:
                        cat = ser.find(f"{C}xVal")
                    val = ser.find(f"{C}val")
                    if val is None:
                        val = ser.find(f"{C}yVal")
                    categorie = [v.text for v in cat.iter(f"{C}v")] if cat is not None else []
                    valori = [v.text for v in val.iter(f"{C}v")] if val is not None else []
                    if not valori:
                        # file salvati senza cache dei valori: resta solo il riferimento alle celle
                        rif = val.find(f".//{C}f") if val is not None else None
                        if rif is None or not rif.text:
                            continue
                        valori, categorie = [f"dati da {rif.text}"], []
                    serie.append((nome, categorie, valori))
                if serie:
                    out.append(_formatta_grafico(titolo.strip(), serie))
            except Exception as e:
                print(f"[Grafici] {name} non leggibile: {e}", file=sys.stderr)
    return out


def _immagini_ooxml(file_path: str, cartella: str) -> list[bytes]:
    import zipfile
    with zipfile.ZipFile(file_path) as zf:
        return [zf.read(n) for n in sorted(zf.namelist()) if n.startswith(cartella) and not n.endswith("/")]


def _ext_da_bytes(data: bytes) -> str:
    """Riconosce il tipo dal contenuto: negli archivi PST gli allegati spesso non hanno nome."""
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:4] == b"PK\x03\x04":
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                nomi = zf.namelist()
        except zipfile.BadZipFile:
            return ""
        for prefisso, ext in (("word/", ".docx"), ("xl/", ".xlsx"), ("ppt/", ".pptx")):
            if any(n.startswith(prefisso) for n in nomi):
                return ext
    return ""


def _allegati(allegati: list[tuple[str, object]], contesto: str) -> list:
    """Immagini → da descrivere; documenti supportati → letti ricorsivamente."""
    from services.vision import is_immagine
    import tempfile
    seg = []
    for nome, data in allegati:
        if not isinstance(data, (bytes, bytearray)):
            # email allegata a un'altra email (extract_msg restituisce un oggetto Message)
            if hasattr(data, "subject"):
                seg.append(f"[Email allegata: {data.subject or ''}]\n{data.body or ''}")
            continue
        if not data:
            continue
        ext = Path(nome).suffix.lower() or _ext_da_bytes(data)
        if ext in IMAGE_EXT or (ext not in SUPPORTED_EXT and is_immagine(data)):
            seg.append(_Img(data, f"{contesto} — allegato {nome}"))
        elif ext in SUPPORTED_EXT and ext != ".pst":
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(data)
            try:
                testo = leggi_file(tmp.name)
            except Exception as e:
                print(f"[Allegati] {nome} non leggibile: {e}", file=sys.stderr)
                testo = ""
            finally:
                os.unlink(tmp.name)
            if testo.strip():
                seg.append(f"[Allegato: {nome}]\n{testo}")
    return seg


def _png_pagina(page) -> bytes:
    buf = io.BytesIO()
    page.to_image(resolution=150).original.save(buf, "PNG")
    return buf.getvalue()


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

    if ext in IMAGE_EXT:
        from services.vision import descrivi_immagine
        d = descrivi_immagine(Path(file_path).read_bytes(), "file immagine caricato nella documentazione di progetto")
        return f"[Immagine] {d}" if d else ""

    if ext == ".pdf":
        import pdfplumber
        seg = []
        with pdfplumber.open(file_path) as pdf:
            for n, page in enumerate(pdf.pages, 1):
                testo = page.extract_text() or ""
                seg.append(testo)
                try:
                    if len(testo.strip()) < PDF_PAGINA_SCANSIONATA and page.images:
                        seg.append(_Img(_png_pagina(page), f"pagina {n} scansionata di un documento di progetto"))
                        continue
                    for im in page.images:
                        bbox = (max(im["x0"], 0), max(im["top"], 0),
                                min(im["x1"], page.width), min(im["bottom"], page.height))
                        if bbox[2] - bbox[0] < PDF_MIN_IMG_PT or bbox[3] - bbox[1] < PDF_MIN_IMG_PT:
                            continue
                        seg.append(_Img(_png_pagina(page.crop(bbox)), f"pagina {n}: {testo[:300]}"))
                except Exception as e:
                    print(f"[PDF] immagini di pagina {n} non estratte: {e}", file=sys.stderr)
        return _componi(seg)

    if ext == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(file_path)
        seg = []
        ultimo = ""

        def immagini_in(el):
            for rid in el.xpath(".//a:blip/@r:embed"):
                part = doc.part.related_parts.get(rid)
                if part is not None and hasattr(part, "blob"):
                    seg.append(_Img(part.blob, ultimo[:300]))

        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = Paragraph(child, doc)
                if p.text.strip():
                    seg.append(p.text)
                    ultimo = p.text
                immagini_in(child)
            elif tag == "tbl":
                rows = [[c.text for c in r.cells] for r in Table(child, doc).rows]
                seg.extend(_tabella_in_righe(rows, "Tabella"))
                immagini_in(child)
        seg.extend(_grafici_ooxml(file_path, "word/charts/"))
        return _componi(seg)

    if ext == ".msg":
        import extract_msg
        msg = extract_msg.Message(file_path)
        try:
            body = msg.body or ""
            if not body.strip() and msg.htmlBody:
                from bs4 import BeautifulSoup
                body = BeautifulSoup(msg.htmlBody, "html.parser").get_text(separator="\n", strip=True)
            seg = [msg.subject or "", body]
            allegati = [(a.longFilename or a.shortFilename or "", a.data) for a in msg.attachments]
            seg.extend(_allegati(allegati, f"email «{msg.subject or ''}»"))
        finally:
            msg.close()
        return _componi(seg)

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

        def _allegati_pst(m) -> list[tuple[str, bytes]]:
            out = []
            try:
                n = m.number_of_attachments
            except Exception:
                return out
            for j in range(n):
                try:
                    att = m.get_attachment(j)
                    size = att.get_size()
                    data = att.read_buffer(size) if size else b""
                    nome = _decode(getattr(att, "name", None))
                    out.append((nome or f"allegato_{j + 1}", data))
                except Exception as e:
                    print(f"[PST] allegato {j} non leggibile: {e}", file=sys.stderr)
            return out

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
                        parts.extend(_allegati(_allegati_pst(msg), f"email «{subject}»"))
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
        return _componi(parts)

    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.extend(_tabella_in_righe(sheet.iter_rows(values_only=True), f"Foglio: {sheet.title}"))
        wb.close()
        extra = _grafici_ooxml(file_path, "xl/charts/")
        extra += [_Img(b, "immagine incorporata in un file Excel di progetto") for b in _immagini_ooxml(file_path, "xl/media/")]
        if extra:
            parts.append("[Grafici e immagini del file]")
            parts.extend(extra)
        return _componi(parts)

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
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        prs = Presentation(file_path)
        seg = []
        for n, slide in enumerate(prs.slides, 1):
            title_shape = slide.shapes.title
            titolo = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
            seg.append(f"[Slide {n}{': ' + titolo if titolo else ''}]")
            title_id = title_shape.shape_id if title_shape is not None else None
            testo_slide = []

            def visita(shapes):
                for sh in shapes:
                    try:
                        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                            visita(sh.shapes)
                            continue
                        if sh.has_text_frame and sh.text_frame.text.strip() and sh.shape_id != title_id:
                            seg.append(sh.text_frame.text)
                            testo_slide.append(sh.text_frame.text)
                        if getattr(sh, "has_table", False):
                            rows = [[c.text for c in r.cells] for r in sh.table.rows]
                            seg.extend(_tabella_in_righe(rows, "Tabella"))
                        if getattr(sh, "has_chart", False):
                            seg.append(_grafico_pptx(sh.chart))
                        image = getattr(sh, "image", None)  # solo Picture e PlaceholderPicture
                        if image is not None:
                            ctx = f"slide {n} «{titolo}»: " + " ".join(testo_slide)
                            seg.append(_Img(image.blob, ctx[:400]))
                    except Exception as e:
                        print(f"[PPTX] slide {n}, forma non leggibile: {e}", file=sys.stderr)

            visita(slide.shapes)
            if slide.has_notes_slide:
                note = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
                if note:
                    seg.append(f"[Note del relatore] {note}")
        return _componi(seg)

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
    if not full_text.strip():
        return {"source": source_name, "chunks": 0,
                "warning": "Nessun contenuto estratto (file vuoto o solo immagini decorative)"}
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
