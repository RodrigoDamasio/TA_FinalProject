"""SEARCH_MODE=fake: deterministic web search and page fetching (0 network). Also the base
of the test fakes. Owner: SEARXNG (BACKEND_PLAN §4)."""

from app.domain.errors import FetchFailed, SearchUnavailable
from app.domain.ports import FetchedPage, WebResult


def demo_results(query: str, n: int = 3) -> list[WebResult]:
    topic = " ".join(query.split()[:8]) or "the topic"
    slug = "-".join(w.lower() for w in topic.split()[:5] if w.isalnum()) or "topic"
    angles = ("overview", "trade-offs", "practical guide", "common pitfalls", "benchmarks")
    return [
        WebResult(
            f"{topic}: {angle}",
            f"https://example.org/{slug}/{angle.replace(' ', '-')}",
            f"Demo search result about {topic}, covering its {angle}. In demo mode this text "
            "stands in for a real web page snippet.",
            ("demo",),
        )
        for angle in angles[:n]
    ]


class FakeWebSearch:
    """`results` maps a query to its results; unknown queries get demo results."""

    def __init__(
        self,
        results: dict[str, list[WebResult]] | None = None,
        *,
        fail: bool = False,
        empty: bool = False,
    ) -> None:
        self.results = dict(results or {})
        self.fail, self.empty = fail, empty
        self.queries: list[str] = []

    def search(self, query: str, limit: int) -> list[WebResult]:
        self.queries.append(query)
        if self.fail:
            raise SearchUnavailable("demo: web search is down")
        if self.empty:
            return []
        return (self.results.get(query) or demo_results(query))[:limit]

    def available(self) -> bool:
        return not self.fail


class FakePageFetcher:
    def __init__(self, pages: dict[str, FetchedPage] | None = None) -> None:
        self.pages = dict(pages or {})
        self.urls: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        if url in self.pages:
            return self.pages[url]
        if url.startswith("https://example.org/"):
            body = (
                "<html><body><h1>Demo page</h1><p>The full demo page behind a search result. "
                "It repeats the topic with more detail for read_result.</p></body></html>"
            )
            return FetchedPage(url, 200, "text/html", body)
        raise FetchFailed("http 404")
