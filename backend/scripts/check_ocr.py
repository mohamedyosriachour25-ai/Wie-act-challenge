"""Check that the OCR model reads a scanned PDF page with your real key.

    cd backend
    python scripts/check_ocr.py path/to/scanned.pdf [page number, default 1]

Reads backend/.env like the app does, renders the page, sends it to HERWIEGO_OCR_MODEL (default deepseek-ocr)
and prints the first part of the text. Costs one call. Never prints your key.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.gateway import AIError, Gateway  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.materials import page_needs_ocr, pdf_pages, render_pdf_page  # noqa: E402


async def main(path: str, page: int) -> int:
    settings = load_settings()
    data = Path(path).read_bytes()
    try:
        texts = pdf_pages(data)
    except ValueError as exc:
        texts = []
        print(f"pypdf: {exc} (pdfium will still try to render it)")
    if texts:
        layer = "needs OCR" if page_needs_ocr(texts[page]) else "has a readable text layer (OCR not needed)"
        print(f"{len(texts)} page(s). Page {page + 1} {layer}.")
    img = render_pdf_page(data, page)
    if not img:
        print("That page is blank.")
        return 0
    print(f"Sending page {page + 1} ({len(img) // 1024} KB JPEG) to the OCR model '{settings.ocr_model}'...")
    gw = Gateway(settings)
    try:
        text = await gw.ocr(img)
    except AIError as exc:
        print(f"FAILED ({exc.kind}, status {exc.status}): {exc.message}")
        print("If the model id is wrong, set HERWIEGO_OCR_MODEL in backend/.env to the id listed by GET /v1/models.")
        return 1
    finally:
        await gw.aclose()
    print(f"OK, {len(text)} characters read:\n" + "-" * 60 + "\n" + text[:1200])
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], max(0, int(sys.argv[2]) - 1) if len(sys.argv) > 2 else 0)))
