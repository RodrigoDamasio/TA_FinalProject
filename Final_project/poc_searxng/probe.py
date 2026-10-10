"""Probe which SearXNG engines answer from the current network, and how well.

Runs a fixed set of research-style queries, first against each engine on
its own, then against the default engine mix, and prints a JSON report.
"Answered" alone is not enough (an engine can return off-topic pages), so
each result is also scored for relevance with a simple keyword heuristic.

Standard library only, so it runs inside the SearXNG container
(`railway ssh -s searxng -- python3 /opt/probe/probe.py`) as well as locally.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QUERIES = [
    "retrieval augmented generation vs fine-tuning",
    "transactional outbox pattern",
    "HMAC webhook signature verification",
    "ChromaDB hybrid search BM25",
    "reciprocal rank fusion",
    "FastAPI server-sent events",
    "Gemini API rate limits free tier",
    "multi-agent supervisor pattern LLM",
    "prompt injection mitigation",
    "SSRF protection private IP ranges",
]

ENGINES = [
    "duckduckgo",
    "brave",
    "bing",
    "google",
    "qwant",
    "yahoo",
    "yep",
    "mwmbl",
    "yandex",
    "seznam",
    "wikipedia",
]

STOPWORDS = {"vs", "and", "the", "of", "for", "llm", "api"}


def keywords(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOPWORDS]


def is_relevant(result: dict, words: list[str]) -> bool:
    """Relevant = title + snippet contain all but one of the query's keywords (at least 2)."""
    text = f"{result.get('title', '')} {result.get('content', '')}".lower()
    return sum(w in text for w in words) >= max(2, len(words) - 1)


def search(base: str, query: str, engines: str | None, language: str, timeout: float) -> dict:
    params = {"q": query, "format": "json", "language": language}
    if engines:
        params["engines"] = engines
    url = f"{base}/search?{urllib.parse.urlencode(params)}"
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": type(exc).__name__, "seconds": round(time.monotonic() - started, 2)}
    results = body.get("results", [])
    words = keywords(query)
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "results": len(results),
        "relevant": sum(1 for r in results if is_relevant(r, words)),
        "with_snippet": sum(1 for r in results if r.get("content")),
        "top": [{"title": r.get("title", "")[:80], "url": r.get("url", "")} for r in results[:3]],
        "unresponsive": [[name, reason] for name, reason in body.get("unresponsive_engines", [])],
    }


def summarize(runs: list[dict], prefix_reasons: bool = False) -> dict:
    answered = [r for r in runs if r["ok"] and r["results"] > 0]
    total = sum(r["results"] for r in answered)
    relevant = sum(r["relevant"] for r in answered)
    reasons = sorted(
        {f"{n}: {reason}" if prefix_reasons else reason for r in runs if r["ok"] for n, reason in r["unresponsive"]}
    )
    return {
        "answered": f"{len(answered)}/{len(runs)}",
        "avg_results": round(total / len(answered), 1) if answered else 0,
        "relevant_pct": round(100 * relevant / total) if total else 0,
        "avg_seconds": round(sum(r["seconds"] for r in runs) / len(runs), 2),
        "failure_reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--engines", default=",".join(ENGINES))
    parser.add_argument("--language", default="en", help="SearXNG language code, e.g. en, en-US, all")
    parser.add_argument("--queries", type=int, default=len(QUERIES))
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    engines = [e for e in args.engines.split(",") if e]
    queries = QUERIES[: args.queries]
    per_engine: dict[str, list[dict]] = {e: [] for e in engines}
    mixed: list[dict] = []

    for i, query in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] {query}", file=sys.stderr)
        for engine in engines:
            per_engine[engine].append(search(args.base, query, engine, args.language, args.timeout))
            time.sleep(args.delay)
        mixed.append(search(args.base, query, None, args.language, args.timeout))
        time.sleep(args.delay)

    summary = {engine: summarize(runs) for engine, runs in per_engine.items()}
    summary["(default mix)"] = summarize(mixed, prefix_reasons=True)

    json.dump(
        {
            "base": args.base,
            "language": args.language,
            "queries": queries,
            "summary": summary,
            "per_engine": per_engine,
            "mixed": mixed,
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
