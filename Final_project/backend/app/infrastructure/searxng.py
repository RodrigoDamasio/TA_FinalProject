"""Step 3 STUB of the SearXNG adapter (WebSearch port): one GET to the private service,
results mapped to WebResult. SEARXNG_TOOL_PLAN §3.1 adds the cache, breaker, logs and all
the mapping rules. Owner: SEARXNG."""

import httpx

from app.domain.errors import SearchUnavailable
from app.domain.ports import WebResult


class SearxngSearch:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False)

    def search(self, query: str, limit: int) -> list[WebResult]:
        params = {"q": query, "format": "json", "language": "en", "safesearch": "1"}
        try:
            response = self._client.get(f"{self._base}/search", params=params)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as err:
            raise SearchUnavailable(type(err).__name__) from err
        out = []
        for r in body.get("results", []):
            url = str(r.get("url", ""))
            if url.startswith(("http://", "https://")):
                out.append(
                    WebResult(
                        str(r.get("title", "")).strip() or url,
                        url,
                        str(r.get("content", "")).strip(),
                        tuple(r.get("engines", ())),
                    )
                )
        return out[:limit]

    def available(self) -> bool:
        return True
