"""Find out what the challenge platform exposes, with the credentials in backend/.env.

    cd backend
    python scripts/probe_platform.py

It checks, without printing your key:
  1. the models (GET <base>/models) and which of them look like embedding models;
  2. which embeddings model id works (POST <base>/embeddings, one tiny input each -- a few embedding tokens);
  3. where the platform's vector database answers: Qdrant, Chroma, or an OpenAI-style vector-store API,
     on the AI base URL, its host, and the usual ports (6333 Qdrant, 8000 Chroma).
Then it prints the .env lines to use. No generation call is made (no chat quota is spent).
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402

from app.config import load_settings  # noqa: E402

EMBED_HINT = re.compile(r"embed|bge|e5-|gte|minilm|nomic|mxbai|arctic|jina", re.I)


def short(text: str, n: int = 140) -> str:
    return " ".join(str(text).split())[:n]


def main() -> int:
    s = load_settings()
    if not s.base_url or not s.api_key:
        print("HERWIEGO_BASE_URL / HERWIEGO_API_KEY are not set in backend/.env")
        return 1
    base = s.base_url.rstrip("/")
    key = s.api_key
    headers = {"Authorization": f"Bearer {key}", "api-key": key, "X-Chroma-Token": key}
    client = httpx.Client(headers=headers, timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True)
    print(f"AI base URL: {base}\n")

    # 1. models -------------------------------------------------------------------------------
    models: list[str] = []
    try:
        r = client.get(f"{base}/models")
        models = [m.get("id", "") for m in (r.json().get("data") or [])] if r.status_code == 200 else []
        print(f"[models] HTTP {r.status_code}: {', '.join(models) or short(r.text)}")
    except Exception as exc:
        print(f"[models] failed: {type(exc).__name__}")

    # 2. embeddings -------------------------------------------------------------------------
    candidates = list(dict.fromkeys([s.embed_model] + [m for m in models if EMBED_HINT.search(m)]
                                    + ["text-embedding-3-small", "nomic-embed-text", "bge-m3", "all-minilm", "embed-small"]))
    working = None
    print("\n[embeddings]")
    for model in candidates:
        try:
            r = client.post(f"{base}/embeddings", json={"model": model, "input": ["probe"]})
            if r.status_code == 200:
                dim = len((r.json().get("data") or [{}])[0].get("embedding") or [])
                print(f"  OK   {model}  (vector size {dim})")
                working = working or model
            else:
                print(f"  {r.status_code}  {model}: {short(r.text, 100)}")
        except Exception as exc:
            print(f"  ERR  {model}: {type(exc).__name__}")
    if not working:
        print("  No embeddings model answered. Ask the organisers for the embeddings model id; until then the app ranks by keywords.")

    # 3. vector database ----------------------------------------------------------------------
    parts = urlsplit(base)
    origin = f"{parts.scheme}://{parts.netloc}"
    host = f"{parts.scheme}://{parts.hostname}"
    roots = list(dict.fromkeys([u for u in (s.vector_url, base, origin, f"{host}:6333", f"{host}:8000") if u]))
    checks = [
        ("qdrant", "/collections", lambda j: isinstance(j, dict) and "collections" in (j.get("result") or {})),
        ("chroma", "/api/v2/heartbeat", lambda j: isinstance(j, dict) and any("heartbeat" in k for k in j)),
        ("chroma (v1, not supported yet)", "/api/v1/heartbeat", lambda j: isinstance(j, dict) and any("heartbeat" in k for k in j)),
        ("openai vector stores (not supported yet)", "/vector_stores", lambda j: isinstance(j, dict) and j.get("object") == "list"),
    ]
    found = []
    print("\n[vector database]")
    for root in roots:
        for name, path, ok in checks:
            url = root.rstrip("/") + path
            try:
                r = client.get(url)
                body = r.json() if "json" in r.headers.get("content-type", "") else None
                hit = r.status_code == 200 and ok(body)
                print(f"  {'FOUND' if hit else r.status_code:>5}  {name:<40} {url}")
                if hit:
                    found.append((name, root, body))
            except Exception as exc:
                print(f"  {'--':>5}  {name:<40} {url}  ({type(exc).__name__})")

    # summary -----------------------------------------------------------------------------------
    print("\n=== Suggested backend/.env lines ===")
    if working:
        print(f"HERWIEGO_EMBED_MODEL={working}")
        print("HERWIEGO_SEMANTIC_CACHE=1")
    else:
        print("HERWIEGO_SEMANTIC_CACHE=0")
    supported = [f for f in found if f[0] in ("qdrant", "chroma")]
    if supported:
        name, root, body = supported[0]
        print(f"HERWIEGO_VECTOR_BACKEND={name}")
        print(f"HERWIEGO_VECTOR_URL={root}")
        if name == "qdrant":
            names = [c.get("name") for c in (body.get("result") or {}).get("collections", [])]
            print(f"HERWIEGO_VECTOR_COLLECTION={names[0] if len(names) == 1 else '<your team collection>'}   # existing: {names}")
        else:
            print("HERWIEGO_VECTOR_COLLECTION=<your team collection>")
    elif found:
        print(f"# Found {found[0][0]} at {found[0][1]} -- send this output to the developer to add that backend.")
    else:
        print("# No vector database answered. Keep HERWIEGO_VECTOR_BACKEND=local and ask the organisers for its URL.")
    print("\n(Paste this whole output when asking for help; it contains no key.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
