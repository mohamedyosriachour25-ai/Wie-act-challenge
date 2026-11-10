"""Web lookup for the study-space tutor: a DECLARED external source (guide: an additional external
service is acceptable only if clearly declared). It is shown to the learner as "Wikipedia" on every
answer that used it, and it can be switched off per message or for the whole app (HERWIEGO_WEB_SEARCH=0).

Why Wikipedia: it needs no key or account, its content is openly licensed (CC BY-SA, so every extract is
linked back to its article), and it covers the reference facts course notes usually lack. Only the
learner's rewritten question is sent, never their material, and never a message that looks like it
contains personal details (the caller checks that first).

A slow or failed lookup returns no results instead of an error: the tutor still answers from the
material and general knowledge, so the web can never be the reason a learner waits.
"""
from __future__ import annotations

import asyncio
import re
import time

import httpx

WIKI = {"en": "en", "fr": "fr", "ar": "ar", "darija": "ar"}   # Darija learners read Arabic Wikipedia
USER_AGENT = "HerwieGO/1.0 (IEEE WIE ACT 5.0 education project; contact wie.act@ieee.tn)"
CACHE_TTL_S = 6 * 3600


class WebLookup:
    def __init__(self, timeout_s: float = 3.5, transport: httpx.AsyncBaseTransport | None = None):
        self.timeout_s = timeout_s
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_s, connect=min(2.0, timeout_s)),
            transport=transport,
            follow_redirects=True,
        )
        self.cache: dict[str, tuple[float, list[dict]]] = {}
        self.calls = 0
        self.failures = 0

    async def search(self, query: str, lang: str = "en", limit: int = 2) -> list[dict]:
        """Up to `limit` article extracts: [{"title", "text", "url"}]. Never raises."""
        query = " ".join((query or "").split())[:200]
        if len(query) < 3:
            return []
        wiki = WIKI.get(lang, "en")
        key = f"{wiki}:{query.lower()}"
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
        params = {
            "action": "query", "format": "json", "formatversion": "2", "utf8": "1", "redirects": "1",
            "generator": "search", "gsrsearch": query, "gsrlimit": str(limit), "gsrnamespace": "0",
            "prop": "extracts|info", "inprop": "url", "exintro": "1", "explaintext": "1", "exchars": "1200",
        }
        self.calls += 1
        try:
            resp = await asyncio.wait_for(self.client.get(f"https://{wiki}.wikipedia.org/w/api.php", params=params), self.timeout_s)
            resp.raise_for_status()
            pages = (resp.json().get("query") or {}).get("pages") or []
            if isinstance(pages, dict):          # formatversion=1 shape, should the parameter ever be dropped
                pages = list(pages.values())
        except Exception:
            self.failures += 1
            return []
        pages = sorted(pages, key=lambda p: p.get("index", 99))
        out = []
        for p in pages[:limit]:
            text = re.sub(r"\s+", " ", p.get("extract") or "").strip()
            if len(text) < 60:
                continue
            title = str(p.get("title", ""))[:120]
            url = p.get("fullurl") or f"https://{wiki}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            out.append({"title": title, "text": text[:1200], "url": url})
        self.cache[key] = (time.time(), out)
        if len(self.cache) > 500:
            for old in sorted(self.cache, key=lambda k: self.cache[k][0])[:100]:
                self.cache.pop(old, None)
        return out

    async def aclose(self) -> None:
        await self.client.aclose()
