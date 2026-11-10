"""Course material: upload, index in the background, retrieve.

Scopes: "me" is a learner's personal library, "space-<id>" a study space's, a room code a room's.

RAG workflow (guide: narrow with retrieval before the mid-size call):
  upload -> paragraph-aware chunks (one sentence of overlap) -> embeddings, batched and cached by content
  (a passage is never embedded twice) -> the platform vector database, one collection for the team
  (`vectors.py`; a local mirror answers if it is unreachable) -> at question time: hybrid ranking
  (cosine + keyword overlap), neighbouring chunks merged, top passages only reach the model.
Without any embeddings endpoint, documents still work through keyword ranking alone.

Only text that could be public may be sent to the inference service, so uploads that look like they
contain personal data (e-mail addresses, phone numbers) have those details removed before indexing.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
from collections import Counter
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from . import scoring, security
from .ai import pipeline, prompts
from .ai.gateway import AIError
from .auth import current_user, user_from_token
from .i18n import lang_of, tr

log = logging.getLogger("herwiego")

router = APIRouter(prefix="/api/materials", tags=["materials"])

MAX_UPLOAD_BYTES = 3_000_000
MAX_TEXT_CHARS = 300_000
MAX_DOCS = 6
MAX_CHUNKS_DOC = 240
MAX_CHUNKS_COLLECTION = 600
CHUNK_CHARS = 800
MIN_TEXT_CHARS = 80
MAX_IMAGES = 12
FILE_MIME = {"pdf": "application/pdf", "txt": "text/plain", "text": "text/plain", "csv": "text/csv", "md": "text/markdown", "markdown": "text/markdown"}
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
# Magic numbers, so a mislabelled or disguised file is rejected before it is stored or served.
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))


def sniff_image(ext: str, data: bytes) -> str | None:
    """Return the real MIME type if the bytes are a supported image, else None."""
    for magic, mime in IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+\d{1,3}[ .-]?)\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}(?!\d)")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{9,}(?!\d)")


# ------------------------------------------------------------------ text prep
SCANNED_PAGE_CHARS = 40   # a PDF page with less text than this is treated as a scanned image and read by OCR


def page_needs_ocr(text: str) -> bool:
    """No text layer, or one that decodes to garbage (fonts without a character map give runs of symbols,
    replacement characters or "(cid:12)" codes): read the page image instead."""
    t = text.strip()
    if len(t) < SCANNED_PAGE_CHARS:
        return True
    if t.count("\ufffd") + t.count("(cid:") * 5 > len(t) * 0.05:
        return True
    visible = [c for c in t if not c.isspace()]
    letters = sum(c.isalpha() for c in visible)
    return letters < 0.3 * len(visible)


def pdf_pages(data: bytes) -> list[str]:
    """Text of each page (up to 200). Raises ValueError only when the file is not a readable PDF at all."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")          # many "protected" course PDFs only restrict printing/copying
            except Exception:
                raise ValueError("This PDF is password-protected. Remove the password, or paste the text.")
        out = []
        for page in reader.pages[:200]:
            try:
                out.append(page.extract_text() or "")
            except Exception:              # one damaged page must not lose the whole document
                out.append("")
        return out
    except ValueError:
        raise
    except Exception:
        raise ValueError("This PDF could not be read. Try exporting it again, or paste the text.")


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "\n\n".join(pdf_pages(data))
    if not name.endswith((".txt", ".md", ".markdown", ".text", ".csv")):
        raise ValueError("Add a PDF, a .txt or a .md file, or paste the text.")
    return data.decode("utf-8", errors="replace")


def personal_data_reason(text: str) -> str | None:
    """Used for typed questions (refused, the learner can rephrase). Material is redacted instead."""
    if EMAIL_RE.search(text):
        return "This looks like it contains e-mail addresses."
    if PHONE_RE.search(text) or len(LONG_NUMBER_RE.findall(text)) >= 3:
        return "This looks like it contains phone numbers or identification numbers."
    return None


def redact_personal(text: str) -> tuple[str, int]:
    """Material is often full of contact lines (a teacher's e-mail in every footer). Rather than refuse the whole
    document, remove those details before anything is stored for search or sent to the AI service."""
    n = 0

    def sub(rx, label, t):
        nonlocal n
        t, k = rx.subn(label, t)
        n += k
        return t

    text = sub(EMAIL_RE, "[e-mail removed]", text)
    text = sub(PHONE_RE, "[phone removed]", text)
    text = sub(LONG_NUMBER_RE, "[number removed]", text)
    return text, n


def render_pdf_page(data: bytes, index: int, scale: float = 2.0) -> bytes:
    """One PDF page as a JPEG (about 144 dpi), for the OCR model. Uses pdfium, so it works on any PDF."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        img = pdf[index].render(scale=scale).to_pil().convert("L")   # greyscale: smaller, same OCR quality
        from PIL import ImageStat
        if ImageStat.Stat(img).stddev[0] < 2.0:
            return b""                   # a blank page: nothing to read, no OCR call spent
        if max(img.size) > 2000:
            img.thumbnail((2000, 2000))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80, optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()


def chunk_text(text: str) -> list[str]:
    """Paragraph-aware chunks of about CHUNK_CHARS, with one sentence of overlap between neighbours."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r", ""))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        p = re.sub(r"\s*\n\s*", " ", p)
        if len(p) <= CHUNK_CHARS:
            pieces.append(p)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", p):
            while len(sentence) > CHUNK_CHARS:  # a sentence longer than a chunk: cut it
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(sentence[:CHUNK_CHARS])
                sentence = sentence[CHUNK_CHARS:]
            if current and len(current) + len(sentence) + 1 > CHUNK_CHARS:
                pieces.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > CHUNK_CHARS:
            chunks.append(current)
            tail = re.split(r"(?<=[.!?])\s+", current)[-1]
            current = tail if 0 < len(tail) < 220 else ""
        current = f"{current}\n\n{piece}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c.strip()) >= 20]


def pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# Very common words in the four interface languages: they match everything and rank nothing.
STOPWORDS = frozenset("""
the a an of to in on at for and or is are was were be been it its this that these those with as by from what which who
how why when where does do did can could should would will about into than then there their them they you your we our
le la les un une des du de et ou est sont dans sur pour par avec que qui quoi comment pourquoi quand ce cet cette ces
il elle ils elles nous vous leur leurs au aux en se sa son ses plus pas ne
في من على إلى عن ما ماذا هل هو هي هذا هذه ذلك التي الذي كيف لماذا متى أن إن مع كان كانت ثم أو و
شنو علاش كيفاش وقتاش فما برشا باش متاع
""".split())


def _tokens(s: str) -> list[str]:
    """Word tokens of length >= 2, lowercased, without stopwords. \\w is Unicode-aware (Arabic, accents)."""
    return [w for w in re.findall(r"\w+", s.lower()) if len(w) >= 2 and w not in STOPWORDS]


def _terms(s: str) -> set[str]:
    return set(_tokens(s))


def lexical_score(query: str, text: str) -> float:
    """Fraction of the query's distinct terms that appear in the passage (0..1): an ABSOLUTE coverage measure,
    so a passage sharing one common word with a long question does not look relevant."""
    q = _terms(query)
    if not q:
        return 0.0
    return len(q & _terms(text)) / len(q)


def bm25(query: str, texts: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Okapi BM25 of each passage against the query, over this collection: rare course terms weigh more
    than words every passage contains."""
    q = _terms(query)
    docs = [Counter(_tokens(t)) for t in texts]
    if not q or not docs:
        return [0.0] * len(texts)
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / n or 1.0
    idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t in q for df in [sum(1 for d in docs if t in d)]}
    out = []
    for d in docs:
        dl = sum(d.values()) or 1
        out.append(sum(idf[t] * d[t] * (k1 + 1) / (d[t] + k1 * (1 - b + b * dl / avgdl)) for t in q if d[t]))
    return out


def keyword_relevance(query: str, texts: list[str]) -> list[float]:
    """0..1: half BM25 (relative to the best passage here), half absolute term coverage."""
    raw = bm25(query, texts)
    top = max(raw) if raw else 0.0
    return [0.5 * (r / top if top else 0.0) + 0.5 * lexical_score(query, t) for r, t in zip(raw, texts)]


def resolve_collection(request: Request, user: dict, scope: str) -> str:
    if scope == "me":
        return f"u:{user['id']}"
    if scope.startswith("space-"):   # a study space's own library (owner only)
        from . import spaces  # lazy: spaces imports this module

        return spaces.collection_for_scope(request, user, scope)
    code = scope.upper()
    room = request.app.state.hub.rooms.get(code)
    if room is None or user["id"] not in room.members:
        raise HTTPException(403, tr(lang_of(request), "Join the room first to use its material."))
    return f"r:{code}"


def collection_has_material(db, collection: str) -> bool:
    return db.one("SELECT 1 AS x FROM docs WHERE collection=? AND status='ready' AND kind='text' LIMIT 1", (collection,)) is not None


async def notify_room(app, collection: str) -> None:
    if collection.startswith("r:"):
        from . import rooms  # lazy: rooms imports this module

        room = app.state.hub.rooms.get(collection[2:])
        if room:
            await rooms.broadcast(room, {"type": "materials_changed"})


# -------------------------------------------------------------------- indexing
def _emb_key(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\n{text}".encode("utf-8")).hexdigest()


async def embed_passages(app, texts: list[str]) -> list[list[float]]:
    """Embeddings for passages, through the persistent content cache: only unseen text is sent."""
    db, gw = app.state.db, app.state.gateway
    model = app.state.settings.embed_model
    keys = [_emb_key(model, t) for t in texts]
    found: dict[str, list[float]] = {}
    for i in range(0, len(keys), 400):
        part = keys[i : i + 400]
        rows = db.all(f"SELECT key, emb FROM emb_cache WHERE key IN ({','.join('?' * len(part))})", tuple(part))
        found.update({r["key"]: unpack(r["emb"]) for r in rows})
    gw.metrics.embed_cache_hits += sum(1 for k in keys if k in found)
    missing = [(k, t) for k, t in zip(keys, texts) if k not in found]
    if missing:
        vectors = await gw.embed([t for _, t in missing])
        now = time.time()
        db.executemany("INSERT OR REPLACE INTO emb_cache (key, emb, ts) VALUES (?, ?, ?)", [(k, pack(v), now) for (k, _), v in zip(missing, vectors)])
        found.update({k: v for (k, _), v in zip(missing, vectors)})
    return [found[k] for k in keys]


async def push_vectors(app, doc_id: int) -> bool:
    """Send a document's vectors to the platform index (no-op without one). False on failure: the
    local mirror keeps the document searchable and the next start retries (`docs.vsync`)."""
    store = app.state.vectors
    db = app.state.db
    if store is None:
        return True
    doc = db.one("SELECT collection FROM docs WHERE id=?", (doc_id,))
    if doc is None:
        return True
    rows = db.all("SELECT idx, emb FROM chunks WHERE doc_id=? AND emb IS NOT NULL ORDER BY idx", (doc_id,))
    if not rows:
        return True
    try:
        await store.upsert(doc["collection"], doc_id, [(r["idx"], list(unpack(r["emb"]))) for r in rows])
    except Exception as exc:   # VectorError or anything unexpected from the remote index
        log.warning("vector index: document %s not pushed (%s); local mirror in use", doc_id, exc)
        return False
    db.execute("UPDATE docs SET vsync=1 WHERE id=?", (doc_id,))
    return True


async def forget_vectors(app, doc_ids: list[int]) -> None:
    store = app.state.vectors
    if store is None:
        return
    for doc_id in doc_ids:
        try:
            await store.delete_doc(doc_id)
        except Exception as exc:
            log.warning("vector index: could not delete document %s (%s)", doc_id, exc)


def _later(app, coro) -> None:
    """Run in the background, keeping a reference so the task is not garbage-collected."""
    tasks = getattr(app.state, "bg_tasks", None)
    task = asyncio.create_task(coro)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)


async def ocr_document(app, doc_id: int) -> None:
    """A PDF with scanned pages: read those pages with the OCR model (one call per page, quota-aware through the
    gateway), merge them with the pages that already had text, then chunk and index as usual. If nothing can be
    read, the PDF stays in the library as a file the learner can open."""
    db = app.state.db
    row = db.one("SELECT d.collection, d.message, b.data FROM docs d JOIN blobs b ON b.doc_id=d.id WHERE d.id=?", (doc_id,))
    if not row:
        return
    collection, data = row["collection"], row["data"]
    s = app.state.settings
    try:
        pages = await asyncio.to_thread(pdf_pages, data)
    except ValueError:
        pages = []
    if not pages:
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(data); pages = [""] * min(len(pdf), 200); pdf.close()
        except Exception:
            pages = []
    scanned = [i for i, p in enumerate(pages) if page_needs_ocr(p)][: s.ocr_max_pages]
    read = failed = 0
    done_ocr: set[int] = set()
    async with app.state.ocr_lock:          # one scanned document at a time: pages share the team quota
        for k, i in enumerate(scanned):
            if not db.one("SELECT 1 FROM docs WHERE id=?", (doc_id,)):
                return                       # removed while reading
            db.execute("UPDATE docs SET status='reading', chunks_done=?, chunks_total=?, message=? WHERE id=?",
                       (k, len(scanned), f"Reading scanned page {k + 1} of {len(scanned)}.", doc_id))
            await notify_room(app, collection)
            try:
                img = await asyncio.to_thread(render_pdf_page, data, i)
                if not img:
                    continue
                text = (await app.state.gateway.ocr(img)).strip()
            except AIError as exc:
                log.warning("ocr failed doc=%s page=%s: %s", doc_id, i, exc.message)
                failed += 1
                if exc.kind in ("bad_request", "quota", "network") and read == 0 and failed >= 2:
                    break                    # the OCR model is missing or out of quota: stop spending calls
                continue
            except Exception:
                log.exception("ocr page render failed doc=%s page=%s", doc_id, i)
                failed += 1
                continue
            text = re.sub(r"<\|[^|>]*\|>", "", text)   # strip any special tokens the model echoes
            if text:
                pages[i] = text
                read += 1
                done_ocr.add(i)
    # a page OCR could not improve keeps its own text unless that text is undecodable
    keep = [p for i, p in enumerate(pages)
            if i in done_ocr or (len(p.strip()) >= SCANNED_PAGE_CHARS and p.count("\ufffd") + p.count("(cid:") * 5 <= len(p) * 0.05)]
    full, removed = redact_personal("\n\n".join(keep).strip()[:MAX_TEXT_CHARS])
    if len(full) < MIN_TEXT_CHARS:
        db.execute("UPDATE docs SET status='file', chunks_total=0, chunks_done=0, message=? WHERE id=?",
                   ("The scanned pages could not be read, so this PDF is kept as a file you can open. Paste its text to make it searchable.", doc_id))
        return await notify_room(app, collection)
    chunks = chunk_text(full)[:MAX_CHUNKS_DOC]
    notes = json.dumps({"ocr_pages": read, "ocr_failed": failed, "removed": removed})
    db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    db.executemany("INSERT INTO chunks (doc_id, idx, text) VALUES (?, ?, ?)", [(doc_id, n, c) for n, c in enumerate(chunks)])
    db.execute("UPDATE docs SET status='indexing', chunks_total=?, chunks_done=0, message='', note=? WHERE id=?",
               (len(chunks), notes, doc_id))
    await notify_room(app, collection)
    await index_document(app, doc_id)


async def index_document(app, doc_id: int) -> None:
    db = app.state.db
    row = db.one("SELECT collection FROM docs WHERE id=?", (doc_id,))
    if not row:  # removed before indexing started
        return
    collection = row["collection"]
    try:
        async with app.state.index_lock:  # one document at a time: embeddings quota is shared
            all_rows = db.all("SELECT id, text, emb IS NOT NULL AS has FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,))
            rows = [r for r in all_rows if not r["has"]]  # a restart resumes where it stopped
            done, i, waits = len(all_rows) - len(rows), 0, 0
            while i < len(rows):
                batch = rows[i : i + 32]
                try:
                    vectors = await embed_passages(app, [r["text"] for r in batch])
                except AIError as exc:
                    if exc.kind == "quota" and waits < 4:
                        waits += 1
                        db.execute("UPDATE docs SET status='waiting', message=? WHERE id=?", ("Waiting for the AI quota to refill.", doc_id))
                        await asyncio.sleep(min(exc.retry_after or 5, 60))
                        continue
                    # Embeddings are unavailable (no embeddings model, a rejected request, or an
                    # outage): keep the document usable with keyword search rather than failing it.
                    db.execute(
                        "UPDATE docs SET status='ready', chunks_done=?, message=? WHERE id=?",
                        (len(all_rows), "Added for keyword search (semantic index unavailable).", doc_id),
                    )
                    _later(app, summarise_document(app, doc_id))
                    return await notify_room(app, collection)
                db.executemany("UPDATE chunks SET emb=? WHERE id=?", [(pack(v), r["id"]) for r, v in zip(batch, vectors)])
                done += len(batch)
                i += len(batch)
                db.execute("UPDATE docs SET status='indexing', chunks_done=?, message='' WHERE id=?", (done, doc_id))
            db.execute("UPDATE docs SET status='ready', chunks_done=?, message='' WHERE id=?", (len(all_rows), doc_id))
        await push_vectors(app, doc_id)
        _later(app, summarise_document(app, doc_id))
    except Exception:
        log.exception("indexing failed doc=%s", doc_id)
        db.execute("UPDATE docs SET status='failed', message='Indexing failed. Try adding the document again.' WHERE id=?", (doc_id,))
    await notify_room(app, collection)


async def sync_vectors(app) -> int:
    """Re-send documents whose vectors never reached the platform index (outage, or the index was
    configured after they were added). Runs once at start; returns how many were pushed."""
    if app.state.vectors is None:
        return 0
    pushed = 0
    for doc in app.state.db.all("SELECT DISTINCT d.id FROM docs d JOIN chunks c ON c.doc_id=d.id WHERE d.status='ready' AND d.vsync=0 AND c.emb IS NOT NULL"):
        if await push_vectors(app, doc["id"]):
            pushed += 1
        else:
            break   # the index is down: stop, the next start tries again
    return pushed


# -------------------------------------------------------------------- whole-document analysis
SUMMARY_SCHEMA = '{"subject": str, "summary": str, "concepts": [{"name": str, "simple": str}], "links": [{"from": str, "to": str, "label": str}]}'
SUMMARY_CHARS = 3600


def _sample(chunks: list[str], budget: int = SUMMARY_CHARS) -> str:
    """Passages spread evenly over the WHOLE document (start, middle, end), within the budget."""
    if not chunks:
        return ""
    chunks = [c for c in chunks if not looks_front_matter(c)] or chunks   # the title page teaches nothing
    n = max(1, min(len(chunks), budget // 600))
    picks = sorted({round(i * (len(chunks) - 1) / max(1, n - 1)) for i in range(n)}) if len(chunks) > 1 else [0]
    out, used = [], 0
    for i in picks:
        part = chunks[i][: max(0, budget - used)]
        if part:
            out.append(part)
            used += len(part)
    return "\n[...]\n".join(out)


def _validate_summary(obj: dict) -> dict:
    summary = " ".join(str(obj.get("summary", "")).split())[:600]
    if len(summary) < 20:
        raise ValueError("bad summary: too short")
    concepts = []
    for c in (obj.get("concepts") or [])[:10]:
        if isinstance(c, dict):
            name = " ".join(str(c.get("name") or c.get("concept") or c.get("term") or "").split())[:60]
            simple = " ".join(str(c.get("simple") or c.get("explanation") or c.get("definition") or "").split())[:200]
            if name:
                concepts.append({"name": name, "simple": simple})
        elif str(c).strip():
            concepts.append({"name": " ".join(str(c).split())[:60], "simple": ""})
    names = {c["name"].lower() for c in concepts}
    links = []
    for l in (obj.get("links") or [])[:8]:
        if isinstance(l, dict) and str(l.get("from", "")).lower() in names and str(l.get("to", "")).lower() in names:
            links.append({"from": str(l["from"])[:60], "to": str(l["to"])[:60], "label": " ".join(str(l.get("label", "")).split())[:30]})
    return {"subject": " ".join(str(obj.get("subject", "")).split())[:80], "summary": summary, "concepts": concepts, "links": links}


FRONT_MATTER = re.compile(
    r"(\[e-mail removed\]|\[phone removed\]|universit|université|iut\b|département|department|faculty|facult[ée]|école|school of|"
    r"table of contents|table des matières|sommaire|copyright|©|all rights reserved|chapitre \d+\s*$|page \d+)", re.I)


def looks_front_matter(text: str) -> bool:
    """A title page, author line or table of contents: few real sentences, many name/institution markers."""
    t = " ".join(text.split())
    sentences = len(re.findall(r"[a-zà-ÿ][^.!?]{25,}[.!?]", t))
    markers = len(FRONT_MATTER.findall(t))
    dotted = len(re.findall(r"\.{4,}|…{2,}", t))      # "Introduction ......... 3"
    return (markers >= 2 and sentences <= 2) or dotted >= 3 or (len(t) < 220 and sentences == 0)


FRONT_MATTER_WEIGHT = 0.35   # such passages can still be found by name, but never outrank real content


async def summarise_document(app, doc_id: int, force: bool = False) -> bool:
    """One compact call per document, once: what it covers and its key concepts. Used by the tutor to know
    the whole library, not only the passages a search returns. Failures are silent (search still works)."""
    db, gw = app.state.db, app.state.gateway
    doc = db.one("SELECT id, title, kind, summary FROM docs WHERE id=?", (doc_id,))
    if doc is None or doc["kind"] != "text" or (doc["summary"] and not force):
        return False
    chunks = [r["text"] for r in db.all("SELECT text FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,))]
    if not chunks:
        return False
    try:
        # Extracting a document's concepts and how they connect is synthesis: the mid-size class, once per document.
        out, _ = await gw.chat_json("doc_summary", "mid", prompts.doc_summary_messages(doc["title"], _sample(chunks)),
                                    _validate_summary, max_tokens=700, schema=SUMMARY_SCHEMA, temperature=0.2)
    except AIError:
        return False
    if out is None:
        return False
    db.execute("UPDATE docs SET summary=? WHERE id=?", (json.dumps(out, ensure_ascii=False), doc_id))
    return True


def library_overview(db, collection: str, limit: int = 6) -> list[dict]:
    """[{title, subject, summary, concepts}] of the analysed documents of a library, newest first."""
    out = []
    for r in db.all("SELECT title, summary FROM docs WHERE collection=? AND kind='text' AND status='ready' AND summary<>'' ORDER BY id DESC LIMIT ?",
                    (collection, limit)):
        try:
            s = json.loads(r["summary"])
        except ValueError:
            continue
        out.append({"title": r["title"], **s})
    return out


HYBRID_COSINE = 0.8   # final score = 0.8 x cosine + 0.2 x keyword overlap (exact terms: names, formulas, symbols)


async def retrieve(app, collection: str, query: str, k: int = 6) -> list[dict]:
    """Rank a collection's passages against the query.

    1. Vector search: the platform index when configured (top 3k candidates), else the local mirror;
       an unreachable platform index falls back to the mirror (counted in metrics).
    2. Hybrid score: cosine blended with keyword overlap, so exact terms the embedding blurs still count.
       Without embeddings at all, keyword overlap alone ranks the passages.
    3. Neighbouring chunks of the same document (they overlap by one sentence) are merged into one passage,
       so the model gets contiguous context and no duplicated sentence.
    """
    db, gw = app.state.db, app.state.gateway
    rows = db.all(
        "SELECT c.idx, c.text, c.emb, d.id AS doc_id, d.title, d.vsync FROM chunks c JOIN docs d ON d.id=c.doc_id"
        " WHERE d.collection=? AND d.status='ready'",
        (collection,),
    )
    if not rows:
        return []
    by_key = {(r["doc_id"], r["idx"]): r for r in rows}
    cosine_of: dict[tuple[int, int], float] = {}
    if any(r["emb"] is not None for r in rows):
        try:
            qv = (await gw.embed([pipeline.clean_topic(query)]))[0]
            store = app.state.vectors
            remote_ok = False
            if store is not None:
                try:
                    hits = await store.search(collection, qv, k * 3)
                    cosine_of = {(d, i): s for d, i, s in hits if (d, i) in by_key}
                    # documents not in the platform index yet (being synced) are searched on the mirror
                    cosine_of.update({key: cosine(qv, unpack(r["emb"])) for key, r in by_key.items() if not r["vsync"] and r["emb"] is not None})
                    remote_ok = True
                    gw.metrics.vector_searches += 1
                except Exception as exc:
                    gw.metrics.vector_fallbacks += 1
                    log.warning("vector index unreachable (%s): searching the local mirror", exc)
            if not remote_ok:
                cosine_of = {key: cosine(qv, unpack(r["emb"])) for key, r in by_key.items() if r["emb"] is not None}
        except AIError:
            cosine_of = {}   # embeddings went away: keyword ranking below

    keys_all = list(by_key)
    lex = dict(zip(keys_all, keyword_relevance(query, [by_key[k]["text"] for k in keys_all])))
    if cosine_of:
        keys = set(cosine_of) | {key for key, v in sorted(lex.items(), key=lambda kv: -kv[1])[:k] if v > 0}
        scored = {key: HYBRID_COSINE * cosine_of.get(key, _local_cosine(by_key[key], qv)) + (1 - HYBRID_COSINE) * lex[key] for key in keys}
    else:
        scored = {key: v for key, v in lex.items() if v > 0}
    scored = {key: v * (FRONT_MATTER_WEIGHT if looks_front_matter(by_key[key]["text"]) else 1.0) for key, v in scored.items()}
    top = sorted(scored.items(), key=lambda kv: -kv[1])[:k]
    return _merge_neighbours([(by_key[key], score) for key, score in top])


def _local_cosine(row, qv) -> float:
    return cosine(qv, unpack(row["emb"])) if row["emb"] is not None else 0.0


def _merge_neighbours(ranked: list[tuple[dict, float]]) -> list[dict]:
    """Join chunks i and i+1 of one document that both made the cut (they share a sentence)."""
    out: list[dict] = []
    for row, score in ranked:
        joined = False
        for c in out:
            if c["doc_id"] == row["doc_id"] and row["idx"] in (c["first"] - 1, c["last"] + 1):
                if row["idx"] == c["last"] + 1:
                    c["text"], c["last"] = _join(c["text"], row["text"]), row["idx"]
                else:
                    c["text"], c["first"] = _join(row["text"], c["text"]), row["idx"]
                c["score"] = max(c["score"], score)
                joined = True
                break
        if not joined:
            out.append({"doc_id": row["doc_id"], "title": row["title"], "idx": row["idx"], "first": row["idx"], "last": row["idx"], "text": row["text"], "score": score})
    for c in out:
        c.pop("first"), c.pop("last")
    return out


def _join(a: str, b: str) -> str:
    """a then b without repeating the sentence they overlap on."""
    tail = re.split(r"(?<=[.!?])\s+", a.strip())[-1]
    if tail and b.lstrip().startswith(tail):
        b = b.lstrip()[len(tail):]
    return f"{a.rstrip()}\n\n{b.lstrip()}".strip()


# ---------------------------------------------------------------------- routes
def _doc_out(d: dict) -> dict:
    out = {k: d[k] for k in ("id", "title", "status", "chunks_total", "chunks_done", "message", "created_at")}
    try:
        out["note"] = json.loads(d["note"]) if "note" in d.keys() and d["note"] else {}
    except ValueError:
        out["note"] = {}
    out["kind"] = d["kind"] if "kind" in d.keys() else "text"
    mime = d["file_mime"] if "file_mime" in d.keys() else None
    out["has_file"] = bool(mime)   # an original PDF/file is kept and can be opened via /{id}/raw
    out["mime"] = mime
    return out


@router.get("")
def list_documents(request: Request, scope: str = "me"):
    user = current_user(request)
    col = resolve_collection(request, user, scope)
    docs = request.app.state.db.all(
        "SELECT d.*, b.mime AS file_mime FROM docs d LEFT JOIN blobs b ON b.doc_id=d.id WHERE d.collection=? ORDER BY d.created_at DESC",
        (col,),
    )
    return {"documents": [_doc_out(d) for d in docs]}


@router.post("/file")
async def upload(request: Request, scope: str = "me", title: str = ""):
    """Raw body upload; the file name arrives in X-Filename. Indexing continues in the background."""
    user = current_user(request)
    lang = lang_of(request)
    col = resolve_collection(request, user, scope)
    filename = unquote(request.headers.get("x-filename", "material.txt"))[:120]
    is_pdf = filename.lower().endswith(".pdf")
    cap = request.app.state.settings.max_pdf_bytes if is_pdf else MAX_UPLOAD_BYTES
    too_big = tr(lang, "That file is too large. The limit is {n} MB.", n=round(cap / 1024 / 1024))
    if int(request.headers.get("content-length") or 0) > cap:
        raise HTTPException(413, too_big)
    data = await security.read_capped(request, cap)  # stops reading as soon as the cap is passed
    if data is None:
        raise HTTPException(413, too_big)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    db = request.app.state.db

    if ext in IMAGE_TYPES:  # a photo or scan: kept as a visual resource, not sent to the AI or indexed
        mime = sniff_image(ext, data)
        if not mime:
            raise HTTPException(400, tr(lang, "That image could not be read. Use a PNG, JPG, WebP or GIF."))
        if db.one("SELECT COUNT(*) AS n FROM docs WHERE collection=? AND kind='image'", (col,))["n"] >= MAX_IMAGES:
            raise HTTPException(400, tr(lang, "You can keep {n} images here. Remove one first.", n=MAX_IMAGES))
        name = " ".join((title or filename.rsplit(".", 1)[0]).split())[:80] or "Image"
        cur = db.execute(
            "INSERT INTO docs (collection, title, owner_id, status, kind, chunks_total, created_at) VALUES (?, ?, ?, 'ready', 'image', 0, ?)",
            (col, name, user["id"], time.time()),
        )
        db.execute("INSERT INTO blobs (doc_id, mime, data) VALUES (?, ?, ?)", (cur.lastrowid, mime, data))
        await notify_room(request.app, col)
        return _doc_out(db.one("SELECT d.*, b.mime AS file_mime FROM docs d LEFT JOIN blobs b ON b.doc_id=d.id WHERE d.id=?", (cur.lastrowid,)))

    scanned = False
    try:
        if is_pdf:
            pages = await asyncio.to_thread(pdf_pages, data)
            text = "\n\n".join(pages)
            # scanned pages (no text layer) are read by OCR in the background
            scanned = any(page_needs_ocr(p) for p in pages) or not pages
            text = "\n\n".join(p for p in pages if not page_needs_ocr(p))
        else:
            text = extract_text(filename, data)
    except ValueError as exc:
        if not is_pdf:
            raise HTTPException(400, tr(lang, str(exc)))
        text, scanned = "", True     # pypdf cannot parse it: pdfium may still render it for OCR
    text, removed = redact_personal(text.strip()[:MAX_TEXT_CHARS])
    if len(text) < MIN_TEXT_CHARS and not scanned:
        raise HTTPException(400, tr(lang, "That is too short to be useful. Add at least a few sentences."))

    if db.one("SELECT COUNT(*) AS n FROM docs WHERE collection=? AND kind='text'", (col,))["n"] >= MAX_DOCS:
        raise HTTPException(400, tr(lang, "You can keep {n} documents here. Remove one first.", n=MAX_DOCS))
    if is_pdf and scanned and request.app.state.settings.ocr_model:
        # Store the PDF now (it shows up immediately) and read the scanned pages in the background.
        name = " ".join((title or filename.rsplit(".", 1)[0]).split())[:80] or "Untitled"
        cur = db.execute(
            "INSERT INTO docs (collection, title, owner_id, status, chunks_total, message, created_at) VALUES (?, ?, ?, 'reading', 0, ?, ?)",
            (col, name, user["id"], "Reading the scanned pages.", time.time()),
        )
        doc_id = cur.lastrowid
        db.execute("INSERT INTO blobs (doc_id, mime, data) VALUES (?, ?, ?)", (doc_id, "application/pdf", data))
        _later(request.app, ocr_document(request.app, doc_id))
        scoring.award(db, user["id"], "material", ref=name[:80])
        await notify_room(request.app, col)
        return _doc_out(db.one("SELECT d.*, b.mime AS file_mime FROM docs d LEFT JOIN blobs b ON b.doc_id=d.id WHERE d.id=?", (doc_id,)))
    if len(text) < MIN_TEXT_CHARS:   # scanned PDF and no OCR model configured: keep it as a file
        name = " ".join((title or filename.rsplit(".", 1)[0]).split())[:80] or "Untitled"
        cur = db.execute(
            "INSERT INTO docs (collection, title, owner_id, status, chunks_total, message, created_at) VALUES (?, ?, ?, 'file', 0, ?, ?)",
            (col, name, user["id"], "This PDF has no text layer, so it is kept as a file you can open. Paste its text to make it searchable.", time.time()),
        )
        db.execute("INSERT INTO blobs (doc_id, mime, data) VALUES (?, ?, ?)", (cur.lastrowid, "application/pdf", data))
        await notify_room(request.app, col)
        return _doc_out(db.one("SELECT d.*, b.mime AS file_mime FROM docs d LEFT JOIN blobs b ON b.doc_id=d.id WHERE d.id=?", (cur.lastrowid,)))

    chunks = chunk_text(text)
    if len(chunks) > MAX_CHUNKS_DOC:
        raise HTTPException(400, tr(lang, "That document is too long for the AI quota ({n} passages, the limit is {max}). Split it into parts.", n=len(chunks), max=MAX_CHUNKS_DOC))
    existing = db.one("SELECT COALESCE(SUM(chunks_total), 0) AS n FROM docs WHERE collection=?", (col,))["n"]
    if existing + len(chunks) > MAX_CHUNKS_COLLECTION:
        raise HTTPException(400, tr(lang, "This library is full. Remove a document before adding another."))

    name = " ".join((title or filename.rsplit(".", 1)[0]).split())[:80] or "Untitled"
    cur = db.execute(
        "INSERT INTO docs (collection, title, owner_id, status, chunks_total, note, created_at) VALUES (?, ?, ?, 'indexing', ?, ?, ?)",
        (col, name, user["id"], len(chunks), json.dumps({"removed": removed}) if removed else "", time.time()),
    )
    doc_id = cur.lastrowid
    db.executemany("INSERT INTO chunks (doc_id, idx, text) VALUES (?, ?, ?)", [(doc_id, i, c) for i, c in enumerate(chunks)])
    mime = FILE_MIME.get(ext)
    if mime:  # keep the uploaded file itself, not only its extracted text, so it can be reopened
        db.execute("INSERT INTO blobs (doc_id, mime, data) VALUES (?, ?, ?)", (doc_id, mime, data))
    asyncio.create_task(index_document(request.app, doc_id))
    scoring.award(db, user["id"], "material", ref=name[:80])
    await notify_room(request.app, col)
    return _doc_out(db.one("SELECT d.*, b.mime AS file_mime FROM docs d LEFT JOIN blobs b ON b.doc_id=d.id WHERE d.id=?", (doc_id,)))


@router.delete("/{doc_id}")
async def remove(doc_id: int, request: Request, scope: str = "me"):
    user = current_user(request)
    col = resolve_collection(request, user, scope)
    db = request.app.state.db
    if not db.one("SELECT id FROM docs WHERE id=? AND collection=?", (doc_id, col)):
        raise HTTPException(404, tr(lang_of(request), "That document does not exist."))
    db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    db.execute("DELETE FROM blobs WHERE doc_id=?", (doc_id,))
    db.execute("DELETE FROM docs WHERE id=?", (doc_id,))
    await forget_vectors(request.app, [doc_id])
    await notify_room(request.app, col)
    return {"ok": True}


@router.get("/{doc_id}/raw")
def raw(doc_id: int, request: Request, scope: str = "me", token: str = ""):
    """Serve an image resource. The token travels in the query string so an <img> tag can load it
    (it is redacted from logs). Access is checked the same way as every other material route."""
    user = user_from_token(request.app, token) or (None if "authorization" not in request.headers else current_user(request))
    if not user:
        raise HTTPException(401, tr(lang_of(request), "Sign in to continue."))
    col = resolve_collection(request, user, scope)
    row = request.app.state.db.one("SELECT b.mime, b.data FROM blobs b JOIN docs d ON d.id=b.doc_id WHERE b.doc_id=? AND d.collection=?", (doc_id, col))
    if not row:
        raise HTTPException(404, tr(lang_of(request), "That document does not exist."))
    title = request.app.state.db.one("SELECT title FROM docs WHERE id=?", (doc_id,))["title"]
    safe = "".join(c for c in title if c.isalnum() or c in " ._-")[:80].strip() or "document"
    return Response(content=row["data"], media_type=row["mime"], headers={
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": f'inline; filename="{safe}"',
    })


class AskBody(BaseModel):
    scope: str = "me"
    question: str


@router.post("/ask")
async def ask(body: AskBody, request: Request):
    user = current_user(request)
    security.spend_ai(request, user["id"])
    col = resolve_collection(request, user, body.scope)
    if not collection_has_material(request.app.state.db, col):
        raise HTTPException(400, tr(lang_of(request), "Add a document and wait until it is ready, then ask your question."))
    try:
        cands = await retrieve(request.app, col, body.question)
        ver = request.app.state.db.one("SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS m FROM docs WHERE collection=? AND status='ready'", (col,))
        cache_key = f"{col}#{ver['n']}.{ver['m']}"
        return await pipeline.ask_material(request.app.state.gateway, body.question, cands, request.app.state.settings.min_similarity, lang_of(request), cache_key=cache_key)
    except ValueError as exc:
        raise HTTPException(400, tr(lang_of(request), str(exc)))
    except AIError as exc:
        from .routes_study import ai_error_response

        return ai_error_response(exc, lang_of(request))
