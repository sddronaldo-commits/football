"""A small, polite HTTP client.

football-data.org's free tier allows 10 requests per minute, and Understat is a
scrape target rather than an API. Both deserve caching and backoff. Responses
are cached to disk so a rerun during development costs nothing and a failed run
resumes instead of re-hammering the source.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

from .config import CACHE_DIR

USER_AGENT = "bigfive-lab/1.0 (portfolio project; contact via repo issues)"


class Fetcher:
    def __init__(self, *, min_interval: float = 6.5, cache_ttl_hours: float = 12.0):
        # 6.5s between calls keeps us under 10/min with headroom for retries.
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl_hours * 3600
        self._last_call = 0.0
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        return CACHE_DIR / f"{key}.txt"

    def get(self, url: str, *, headers: dict | None = None, tries: int = 4) -> str:
        cached = self._cache_path(url)
        if cached.exists() and (time.time() - cached.stat().st_mtime) < self.cache_ttl:
            return cached.read_text(encoding="utf-8")

        delay = 2.0
        for attempt in range(1, tries + 1):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._session.get(url, headers=headers or {}, timeout=30)
                self._last_call = time.time()
                if response.status_code == 429:
                    time.sleep(delay)
                    delay *= 2
                    continue
                response.raise_for_status()
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(response.text, encoding="utf-8")
                return response.text
            except requests.RequestException:
                if attempt == tries:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"unreachable: {url}")
