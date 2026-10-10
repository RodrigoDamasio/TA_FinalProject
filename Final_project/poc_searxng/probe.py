"""Probe which SearXNG engines answer from the current network.

Runs a fixed set of research-style queries, first against each engine on
its own, then against the default engine mix, and prints a JSON report.
Standard library only, so it runs inside the SearXNG container
(`railway ssh -s searxng -- python3 /opt/probe/probe.py`) as well as locally.
"""

import argparse
import json
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
    "wikipedia",
]


def search(base: str, query: str, engines: str | None, timeout: float) -> dict:
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    url = f"{base}/search?{urllib.parse.urlencode(params)}"
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": type(exc).__name__, "seconds": round(time.monotonic() - started, 2)}
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "results": len(body.get("results", [])),
        "with_snippet": sum(1 for r in body.get("results", []) if r.get("content")),
        "unresponsive": [[name, reason] for name, reason in body.get("unresponsive_engines", [])],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--engines", default=",".join(ENGINES))
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
            per_engine[engine].append(search(args.base, query, engine, args.timeout))
            time.sleep(args.delay)
        mixed.append(search(args.base, query, None, args.timeout))
        time.sleep(args.delay)

    summary = {}
    for engine, runs in per_engine.items():
        answered = [r for r in runs if r["ok"] and r["results"] > 0]
        reasons = sorted({reason for r in runs if r["ok"] for _, reason in r["unresponsive"]})
        summary[engine] = {
            "answered": f"{len(answered)}/{len(runs)}",
            "avg_results": round(sum(r["results"] for r in answered) / len(answered), 1) if answered else 0,
            "avg_seconds": round(sum(r["seconds"] for r in runs) / len(runs), 2),
            "failure_reasons": reasons,
        }
    mixed_ok = [r for r in mixed if r["ok"] and r["results"] > 0]
    summary["(default mix)"] = {
        "answered": f"{len(mixed_ok)}/{len(mixed)}",
        "avg_results": round(sum(r["results"] for r in mixed_ok) / len(mixed_ok), 1) if mixed_ok else 0,
        "avg_seconds": round(sum(r["seconds"] for r in mixed) / len(mixed), 2),
        "failure_reasons": sorted({f"{n}: {reason}" for r in mixed if r["ok"] for n, reason in r["unresponsive"]}),
    }

    json.dump(
        {"base": args.base, "queries": queries, "summary": summary, "per_engine": per_engine, "mixed": mixed},
        sys.stdout,
        indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
