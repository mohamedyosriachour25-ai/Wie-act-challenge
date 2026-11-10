"""PDFs as course material: text PDFs, scanned PDFs read by the OCR model, personal details removed, and a
PDF that cannot be read at all still kept as a file the learner can open."""
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.config import load_settings
from app.main import create_app

from ._fake_service import build_fake, fake_ai_settings


def text_pdf(lines: list[str]) -> bytes:
    """A minimal valid PDF with a real text layer (Helvetica), built by hand so the test needs no PDF writer."""
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({l.replace('(', '').replace(')', '')}) '" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def scanned_pdf(pages: int = 2) -> bytes:
    """Image-only pages, like a phone scan: no text layer at all."""
    imgs = []
    for k in range(pages):
        im = Image.new("RGB", (620, 880), "white")
        ImageDraw.Draw(im).text((40, 40), f"Scanned page {k + 1}", fill="black")
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


LINES = [
    "Photosynthesis takes place in the chloroplasts of plant cells.",
    "Chlorophyll absorbs light energy, mostly red and blue light.",
    "The light reactions split water and release oxygen.",
    "The Calvin cycle then builds glucose from carbon dioxide.",
    "Contact: teacher.name@school.tn",
]


def make(tmp_path, **overrides):
    transport, state = build_fake()
    app = create_app(load_settings(db_path=str(tmp_path / "t.db"), **fake_ai_settings(), **overrides), ai_transport=transport)
    return app, state


def signup(c, pseudo="pdf_user"):
    return {"Authorization": "Bearer " + c.post("/api/auth/register", json={"pseudo": pseudo, "password": "secret1"}).json()["token"]}


def put(c, h, data, name):
    return c.post("/api/materials/file?scope=me", content=data, headers={**h, "X-Filename": name})


def settle(c, h, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        docs = c.get("/api/materials?scope=me", headers=h).json()["documents"]
        if docs and all(d["status"] in ("ready", "file", "failed") for d in docs):
            return docs
        time.sleep(0.05)
    raise AssertionError(f"documents did not settle: {docs}")


def chunks_of(app, doc_id):
    return " ".join(r["text"] for r in app.state.db.all("SELECT text FROM chunks WHERE doc_id=? ORDER BY idx", (doc_id,)))


def test_a_text_pdf_with_an_email_is_accepted_and_the_email_never_leaves(tmp_path):
    app, state = make(tmp_path)
    with TestClient(app) as c:
        h = signup(c)
        r = put(c, h, text_pdf(LINES), "course.pdf")
        assert r.status_code == 200, r.text
        assert r.json()["note"] == {"removed": 1}
        doc = settle(c, h)[0]
        assert doc["status"] == "ready" and doc["has_file"]
        text = chunks_of(app, doc["id"])
        assert "chloroplasts" in text and "school.tn" not in text and "[e-mail removed]" in text
        assert all("school.tn" not in str(m) for entry in state.log for m in entry["messages"])
        assert state.ocr_pages == 0            # it had a text layer: no OCR call


def test_a_scanned_pdf_is_read_page_by_page_by_the_ocr_model(tmp_path):
    app, state = make(tmp_path)
    with TestClient(app) as c:
        h = signup(c)
        r = put(c, h, scanned_pdf(2), "scan.pdf")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "reading"         # added at once; the pages are read in the background
        doc = settle(c, h)[0]
        assert doc["status"] == "ready", doc
        assert doc["note"]["ocr_pages"] == 2 and doc["note"]["ocr_failed"] == 0
        assert doc["note"]["removed"] == 2           # the e-mail found on each OCR'd page
        text = chunks_of(app, doc["id"])
        assert "chloroplasts" in text and "uni.tn" not in text
        assert state.ocr_pages == 2
        assert c.get("/api/ai/metrics").json()["ocr_calls"] == 2
        # each OCR call carried one page image, sent to the OCR model only
        ocr = [e for e in state.log if e["model"] == "deepseek-ocr"]
        assert len(ocr) == 2 and ocr[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        # and the material answers questions like any other
        out = c.post("/api/materials/ask", json={"scope": "me", "question": "Where does photosynthesis take place?"}, headers=h).json()
        assert out["grounded"] and "chloroplasts" in out["answer"].lower()


def test_a_pdf_that_cannot_be_read_is_still_kept_as_a_file(tmp_path):
    app, _ = make(tmp_path, ocr_model="")          # no OCR model configured
    with TestClient(app) as c:
        h = signup(c)
        r = put(c, h, scanned_pdf(1), "scan.pdf")
        assert r.status_code == 200 and r.json()["status"] == "file"
        doc = settle(c, h)[0]
        assert doc["status"] == "file" and doc["has_file"]
        raw = c.get(f"/api/materials/{doc['id']}/raw?scope=me", headers=h)
        assert raw.status_code == 200 and raw.content.startswith(b"%PDF")


def test_when_the_ocr_model_is_unavailable_the_pdf_falls_back_to_a_file(tmp_path):
    app, _ = make(tmp_path, ocr_model="not-a-model")   # the service answers 400 "Unknown model."
    with TestClient(app) as c:
        h = signup(c)
        assert put(c, h, scanned_pdf(3), "scan.pdf").status_code == 200
        doc = settle(c, h)[0]
        assert doc["status"] == "file" and doc["has_file"]


def test_large_pdfs_are_allowed_but_other_files_keep_the_small_limit(tmp_path):
    app, _ = make(tmp_path)
    with TestClient(app) as c:
        h = signup(c)
        big = text_pdf(LINES) + b"%" + b"0" * 4_000_000          # a 4 MB PDF (padding after %%EOF is ignored)
        assert put(c, h, big, "big.pdf").status_code == 200
        assert put(c, h, b"x" * 3_100_000, "big.txt").status_code == 413
        assert put(c, h, b"%PDF" + b"0" * (21 * 1024 * 1024), "huge.pdf").status_code == 413
