"""SearxngSearch (WebSearch port, SEARXNG_TOOL_PLAN §3.1): the private SearXNG service.

- Fixed-URL client (configuration, never external input), so it does not use the SSRF guard
  (which would rightly refuse `*.railway.internal`); `trust_env=False` keeps proxies out.
- Mapping: http(s) URLs only, fragments dropped, duplicates removed, title ≤ 200, snippet ≤ 500,
  results without a snippet after those with one; infoboxes/answers ignored.
- Failure → SearchUnavailable: timeout, non-200 (403 = JSON format disabled), bad JSON, or zero
  results while EVERY engine is unresponsive (an engine block is an outage, not "empty").
- Breaker: 3 consecutive failures → open 60 s (no request; cached queries still answer).
- Cache: only non-empty results, keyed by the normalized query, 24 h.
- Logs: query hash and length, counts, engine names + reasons; never the query text.
Owner: SEARXNG."""

import hashlib
import logging
import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.domain.errors import SearchUnavailable
from app.domain.ports import WebResult

logger = logging.getLogger("app.searxng")
FAILURES_TO_OPEN = 3
OPEN_S = 60.0


def normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())[:300]


def _clean_url(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


class SearxngSearch:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cache: Any | None = None,
        cache_ttl_s: float = 86400,
        engines: tuple[str, ...] = ("yahoo", "yandex", "duckduckgo"),
        clock: Callable[[], float] = time.monotonic,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout_s, follow_redirects=False, trust_env=False
        )
        self._cache, self._ttl, self._engines, self._clock = cache, cache_ttl_s, engines, clock
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()
        self.counters = {"requests": 0, "failures": 0, "cache_hits": 0, "unresponsive": 0}
        self._warned_403 = False

    def available(self) -> bool:
        with self._lock:
            return self._clock() >= self._open_until

    def stats(self) -> dict[str, int]:
        return dict(self.counters)

    def search(self, query: str, limit: int) -> list[WebResult]:
        key = hashlib.sha256(("v1|" + normalize_query(query)).encode()).hexdigest()
        q_hash = key[:8]
        if self._cache is not None:
            cached = self._cache.get(key, self._ttl)
            if cached:
                self.counters["cache_hits"] += 1
                return [
                    WebResult(r["title"], r["url"], r["snippet"], tuple(r["engines"]))
                    for r in cached
                ][:limit]
        if not self.available():
            raise SearchUnavailable("breaker open")
        started = time.perf_counter()
        self.counters["requests"] += 1
        try:
            results = self._request(query)
        except SearchUnavailable:
            self._failed()
            raise
        with self._lock:
            self._failures, self._open_until = 0, 0.0
        mapped = self._map(results)[:limit]
        logger.info(
            "searxng q_hash=%s q_len=%d results=%d ms=%d", q_hash, len(query), len(mapped),
            int((time.perf_counter() - started) * 1000),
        )  # fmt: skip
        if mapped and self._cache is not None:
            self._cache.put(key, [
                {"title": r.title, "url": r.url, "snippet": r.snippet, "engines": list(r.engines)}
                for r in mapped
            ])  # fmt: skip
        return mapped

    def _failed(self) -> None:
        with self._lock:
            self.counters["failures"] += 1
            self._failures += 1
            if self._failures >= FAILURES_TO_OPEN:
                self._open_until = self._clock() + OPEN_S
                logger.warning("searxng breaker open for %ds", int(OPEN_S))

    def _request(self, query: str) -> list[dict[str, Any]]:
        params = {"q": query, "format": "json", "language": "en", "safesearch": "1", "pageno": "1"}
        try:
            response = self._client.get(f"{self._base}/search", params=params)
        except httpx.HTTPError as err:
            raise SearchUnavailable(type(err).__name__) from err
        if response.status_code == 403 and not self._warned_403:
            self._warned_403 = True
            logger.error("searxng answered 403: is the json format enabled in settings.yml?")
        if response.status_code != 200:
            raise SearchUnavailable(f"http {response.status_code}")
        try:
            body = response.json()
        except ValueError as err:
            raise SearchUnavailable("invalid json") from err
        unresponsive = body.get("unresponsive_engines") or []
        for engine, reason in unresponsive:
            self.counters["unresponsive"] += 1
            logger.info("searxng engine=%s unresponsive reason=%s", engine, reason)
        results = body.get("results") or []
        blocked = {e for e, _ in unresponsive}
        if not results and self._engines and set(self._engines) <= blocked:
            raise SearchUnavailable("every engine is unresponsive")
        return results

    def _map(self, results: list[dict[str, Any]]) -> list[WebResult]:
        seen: set[str] = set()
        with_snippet: list[WebResult] = []
        without: list[WebResult] = []
        for r in results:
            url = _clean_url(str(r.get("url", "")))
            if url is None or url in seen:
                continue
            seen.add(url)
            title = " ".join(str(r.get("title", "")).split())[:200] or (
                urlsplit(url).hostname or url
            )
            snippet = " ".join(str(r.get("content", "")).split())[:500]
            item = WebResult(title, url, snippet, tuple(r.get("engines") or ()))
            (with_snippet if snippet else without).append(item)
        return with_snippet + without
