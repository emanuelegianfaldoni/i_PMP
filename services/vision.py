import base64
import hashlib
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from services.ai_core import chat_completion

MIN_LATO = 100            # sotto: icone, bullet, separatori
MIN_AREA = 25_000
MAX_PROPORZIONE = 12      # linee e banner molto stretti
MAX_LATO = 1568           # oltre, Claude ridimensiona comunque
MAX_BYTES = 3_500_000     # limite Bedrock ~3.75 MB per immagine
MAX_CACHE = 5000
WORKERS = 4

_DECORATIVA = "DECORATIVA"

_PROMPT = (
    "Sei l'assistente di una piattaforma che archivia la documentazione di progetti IT. "
    "Descrivi questa immagine in italiano, in modo che sia ritrovabile con una ricerca testuale "
    "e utile a chi lavora al progetto.\n"
    "- Trascrivi fedelmente tutto il testo leggibile: titoli, etichette, valori, messaggi di errore.\n"
    "- Grafici: tipo, assi, serie e valori principali.\n"
    "- Diagrammi: componenti e collegamenti tra loro.\n"
    "- Screenshot di applicazioni: quale schermata è, cosa mostra, eventuali errori o dati.\n"
    "- Tabelle: colonne e righe principali.\n"
    "Non inventare nulla che non si veda. Niente premesse: solo la descrizione.\n"
    f"Se l'immagine è solo decorativa (logo, icona, foto di repertorio, sfondo) rispondi soltanto: {_DECORATIVA}\n\n"
    "Contesto in cui compare l'immagine: {contesto}"
)

_cache: dict[str, str | None] = {}
_lock = threading.Lock()


def _prepara(data: bytes) -> tuple[bytes, str] | None:
    """Scarta le immagini troppo piccole e converte in PNG/JPEG entro i limiti di Bedrock."""
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None  # formato non leggibile (es. EMF/WMF, SVG) o non è un'immagine
    w, h = img.size
    if min(w, h) < MIN_LATO or w * h < MIN_AREA or max(w, h) / min(w, h) > MAX_PROPORZIONE:
        return None

    img.thumbnail((MAX_LATO, MAX_LATO))
    buf = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P"):
        img.convert("RGBA").save(buf, "PNG", optimize=True)
        media = "image/png"
    else:
        img.convert("RGB").save(buf, "JPEG", quality=85)
        media = "image/jpeg"
    out = buf.getvalue()
    if len(out) > MAX_BYTES:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=70)
        out, media = buf.getvalue(), "image/jpeg"
    return out, media


def is_immagine(data: bytes) -> bool:
    from PIL import Image
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def descrivi_immagine(data: bytes, contesto: str = "") -> str | None:
    """Descrizione testuale su una riga, oppure None se l'immagine è scartata o decorativa."""
    key = hashlib.sha256(data).hexdigest()
    with _lock:
        if key in _cache:
            return _cache[key]

    prep = _prepara(data)
    descrizione = None
    if prep is not None:
        img_bytes, media = prep
        try:
            raw = chat_completion(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media,
                                                     "data": base64.b64encode(img_bytes).decode()}},
                        {"type": "text", "text": _PROMPT.format(contesto=contesto[:500] or "-")},
                    ],
                }],
                temperature=0.0,
            )
        except Exception as e:
            print(f"[Vision] descrizione fallita: {e}", file=sys.stderr)
            return None  # non in cache: al prossimo caricamento si riprova
        testo = " ".join(raw.split())
        if testo and not testo.upper().startswith(_DECORATIVA):
            descrizione = testo

    with _lock:
        if len(_cache) >= MAX_CACHE:
            _cache.clear()
        _cache[key] = descrizione
    return descrizione


def descrivi_tutte(immagini: list[tuple[bytes, str]]) -> list[str | None]:
    """Descrive in parallelo; le immagini identiche (es. il logo su ogni slide) vengono descritte una volta."""
    if not immagini:
        return []
    unici: dict[str, tuple[bytes, str]] = {}
    chiavi = []
    for data, ctx in immagini:
        k = hashlib.sha256(data).hexdigest()
        chiavi.append(k)
        unici.setdefault(k, (data, ctx))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        risultati = dict(zip(unici, pool.map(lambda item: descrivi_immagine(*item), unici.values())))
    return [risultati[k] for k in chiavi]
