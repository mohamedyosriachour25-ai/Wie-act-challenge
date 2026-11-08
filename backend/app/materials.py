"""Course material: upload, index in the background, retrieve.

Scopes: "me" is a learner's personal library; a room code is the shared library of that room.
The vector store is local (SQLite + cosine) behind `retrieve`, because the guide does not say how the
platform's per-team collection is queried. When that is known, only `retrieve` and `index_document`
need to change.

Only text that could be public may be sent to the inference service, so uploads that look like they
contain personal data (e-mail addresses, phone numbers) are refused.
"""
from __future__ import annotations

import array
import asyncio
import io
import math
import re
import time
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from . import security
from .ai import pipeline
from .ai.gateway import AIError
from .auth import current_user, user_from_token
from .i18n import lang_of, tr

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
def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages[:200]]
        except Exception:
            raise ValueError("This PDF could not be read. Try exporting it again, or paste the text.")
        text = "\n\n".join(pages)
        if len(text.strip()) < MIN_TEXT_CHARS:
            raise ValueError("This PDF has no readable text (it may be scanned images). Paste the text instead.")
        return text
    if not name.endswith((".txt", ".md", ".markdown", ".text", ".csv")):
        raise ValueError("Add a PDF, a .txt or a .md file, or paste the text.")
    return data.decode("utf-8", errors="replace")


def personal_data_reason(text: str) -> str | None:
    if EMAIL_RE.search(text):
        return "This looks like it contains e-mail addresses."
    if PHONE_RE.search(text) or len(LONG_NUMBER_RE.findall(text)) >= 3:
        return "This looks like it contains phone numbers or identification numbers."
    return None


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


# ----------------------------------------------------------------- collections
def resolve_collection(request: Request, user: dict, scope: str) -> str:
    if scope == "me":
        return f"u:{user['id']}"
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
async def index_document(app, doc_id: int) -> None:
    db, gw = app.state.db, app.state.gateway
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
                    vectors = await gw.embed([r["text"] for r in batch])
                except AIError as exc:
                    if exc.kind == "quota" and waits < 4:
                        waits += 1
                        db.execute("UPDATE docs SET status='waiting', message=? WHERE id=?", ("Waiting for the AI quota to refill.", doc_id))
                        await asyncio.sleep(min(exc.retry_after or 5, 60))
                        continue
                    db.execute("UPDATE docs SET status='failed', message=? WHERE id=?", (exc.message, doc_id))
                    return await notify_room(app, collection)
                db.executemany("UPDATE chunks SET emb=? WHERE id=?", [(pack(v), r["id"]) for r, v in zip(batch, vectors)])
                done += len(batch)
                i += len(batch)
                db.execute("UPDATE docs SET status='indexing', chunks_done=?, message='' WHERE id=?", (done, doc_id))
            db.execute("UPDATE docs SET status='ready', chunks_done=?, message='' WHERE id=?", (len(all_rows), doc_id))
    except Exception:
        db.execute("UPDATE docs SET status='failed', message='Indexing failed. Try adding the document again.' WHERE id=?", (doc_id,))
    await notify_room(app, collection)


async def retrieve(app, collection: str, query: str, k: int = 6) -> list[dict]:
    """Similarity search over ready documents of a collection. The query embedding is cached."""
    db, gw = app.state.db, app.state.gateway
    rows = db.all(
        "SELECT c.idx, c.text, c.emb, d.id AS doc_id, d.title FROM chunks c JOIN docs d ON d.id=c.doc_id"
        " WHERE d.collection=? AND d.status='ready' AND c.emb IS NOT NULL",
        (collection,),
    )
    if not rows:
        return []
    qv = (await gw.embed([pipeline.clean_topic(query)]))[0]
    scored = [{"doc_id": r["doc_id"], "title": r["title"], "idx": r["idx"], "text": r["text"], "score": cosine(qv, unpack(r["emb"]))} for r in rows]
    scored.sort(key=lambda c: -c["score"])
    return scored[:k]


# ---------------------------------------------------------------------- routes
def _doc_out(d: dict) -> dict:
    out = {k: d[k] for k in ("id", "title", "status", "chunks_total", "chunks_done", "message", "created_at")}
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
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, tr(lang, "That file is too large. The limit is 3 MB."))
    data = await security.read_capped(request, MAX_UPLOAD_BYTES)  # stops reading as soon as the cap is passed
    if data is None:
        raise HTTPException(413, tr(lang, "That file is too large. The limit is 3 MB."))
    filename = unquote(request.headers.get("x-filename", "material.txt"))[:120]
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

    try:
        text = extract_text(filename, data)
    except ValueError as exc:
        raise HTTPException(400, tr(lang, str(exc)))
    text = text.strip()[:MAX_TEXT_CHARS]
    if len(text) < MIN_TEXT_CHARS:
        raise HTTPException(400, tr(lang, "That is too short to be useful. Add at least a few sentences."))
    reason = personal_data_reason(text)
    if reason:
        raise HTTPException(400, f"{tr(lang, reason)} {tr(lang, 'Material is sent to the AI service, so remove personal details and add it again.')}")

    if db.one("SELECT COUNT(*) AS n FROM docs WHERE collection=? AND kind='text'", (col,))["n"] >= MAX_DOCS:
        raise HTTPException(400, tr(lang, "You can keep {n} documents here. Remove one first.", n=MAX_DOCS))
    chunks = chunk_text(text)
    if len(chunks) > MAX_CHUNKS_DOC:
        raise HTTPException(400, tr(lang, "That document is too long for the AI quota ({n} passages, the limit is {max}). Split it into parts.", n=len(chunks), max=MAX_CHUNKS_DOC))
    existing = db.one("SELECT COALESCE(SUM(chunks_total), 0) AS n FROM docs WHERE collection=?", (col,))["n"]
    if existing + len(chunks) > MAX_CHUNKS_COLLECTION:
        raise HTTPException(400, tr(lang, "This library is full. Remove a document before adding another."))

    name = " ".join((title or filename.rsplit(".", 1)[0]).split())[:80] or "Untitled"
    cur = db.execute(
        "INSERT INTO docs (collection, title, owner_id, status, chunks_total, created_at) VALUES (?, ?, ?, 'indexing', ?, ?)",
        (col, name, user["id"], len(chunks), time.time()),
    )
    doc_id = cur.lastrowid
    db.executemany("INSERT INTO chunks (doc_id, idx, text) VALUES (?, ?, ?)", [(doc_id, i, c) for i, c in enumerate(chunks)])
    mime = FILE_MIME.get(ext)
    if mime:  # keep the uploaded file itself, not only its extracted text, so it can be reopened
        db.execute("INSERT INTO blobs (doc_id, mime, data) VALUES (?, ?, ?)", (doc_id, mime, data))
    asyncio.create_task(index_document(request.app, doc_id))
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
