# POC — SearXNG on Railway

A short spike before any plan builds on SearXNG. The rationale for choosing it is in [BRAINSTORM.md §3](BRAINSTORM.md#3-web-search).

**Status:** local baseline done (2026-10-10). Railway run **pending**: it needs the Railway clean-up first (§5).

## 1. Questions to answer

| # | Question | Why it matters |
|---|---|---|
| Q1 | Which search engines answer from a **Railway IP**? | Engines often CAPTCHA or block cloud-server IPs. This decides SearXNG vs plan B (a search API) |
| Q2 | Are the results usable: enough results, with snippets, fast enough? | The Researcher reads snippets first, and each tool step sits inside a 3-step budget |
| Q3 | Is SearXNG reachable over the **private network** (`searxng.railway.internal:8080`)? | The service gets no public domain, so the backend must reach it privately |
| Q4 | How much **RAM** does it use? | The free plan gives 1 GB per service. It's a separate service, but the project's total still matters |

## 2. Success criteria

SearXNG is kept if, **from Railway**:

- **Engines:** at least **2 web engines** answer **≥ 8 of 10** queries on their own. One working engine is too fragile.
- **The default mix** answers **10/10** queries, with an average of **≥ 5 results** that have snippets.
- **Speed:** average latency **≤ 3 s** per search.
- **Private network:** the private hostname answers `/healthz`.
- **RAM:** under **300 MB**.

**Otherwise: plan B.** A search API (e.g. Tavily or Brave) goes behind the same `SearchProvider` port, with Wikipedia still the fallback. Nothing else in the design changes.

## 3. Setup

Everything is in [`poc_searxng/`](poc_searxng/):

| File | What |
|---|---|
| [`Dockerfile`](poc_searxng/Dockerfile) | **Pinned** image `searxng/searxng:2026.9.25-12f8b6515` + our settings + the probe script |
| [`settings.yml`](poc_searxng/settings.yml) | Starts from SearXNG's defaults (`use_default_settings`). **JSON format only:** HTML returns `403`. Limiter, public-instance features and image proxy off. Short timeouts (4 s, max 8 s). Turns on the engines to test, and turns off `google cse` (needs a key) |
| [`probe.py`](poc_searxng/probe.py) | Standard library only, so it runs **inside the container on Railway**. It runs 10 research-style queries through each engine on its own, then through the default mix, with 1 s between requests, and prints a JSON report including SearXNG's `unresponsive_engines` reasons |

**Secret key:** SearXNG reads `secret_key` from the `SEARXNG_SECRET` environment variable. It's generated at deploy time and never printed.

**Engines tested:** `duckduckgo`, `brave`, `bing`, `google`, `qwant`, `yahoo`, `wikipedia`.

- **Not tested:** Mojeek, Startpage and Dogpile are marked **`inactive`** by SearXNG upstream in this version (they now show proof-of-work CAPTCHAs), so they can't be turned on.
- **Wikipedia:** SearXNG's Wikipedia engine returns an **infobox** for title-like queries, not result items, so it scores 0 results in the probe. That's expected. The backend's fallback uses the Wikipedia API directly (Module 5's client), not this engine.

## 4. Local baseline (home IP, 2026-10-10)

Command: `python3 probe.py --base http://127.0.0.1:8080 > results/local.json`. The raw report is in [`results/local.json`](poc_searxng/results/local.json).

| Engine | Answered | Avg results | Avg time | Failure reason |
|---|---|---|---|---|
| duckduckgo | 0/10 | — | 0.11 s | CAPTCHA |
| brave | 0/10 | — | 0.03 s | too many requests (suspended) |
| bing | **8/10** | 10.0 | 0.26 s | — (2 queries returned 0 results) |
| google | 0/10 | — | 0.06 s | access denied (suspended) |
| qwant | 0/10 | — | 0.34 s | CAPTCHA |
| yahoo | **10/10** | 7.0 | 0.78 s | — |
| wikipedia | 0/10 | — | 0.54 s | — (infobox only, see §3) |
| **default mix** | **10/10** | 16.0 | 0.96 s | duckduckgo, brave, google, qwant as above. `wikidata`: unexpected crash |

**Reading:**
- **The failing engines failed from the very first query.** That points to anti-bot blocking, not our 1 s request rate.
- **Even a home IP gets only 2 working web engines** (Bing and Yahoo). It just meets the "≥ 2 engines" criterion, and Railway is expected to be the same or worse.
- **RAM:** about **120 MB** in Docker after the run.

## 5. Railway run (pending)

**Precondition:** the Railway clean-up decided in the brainstorm. Both current projects (`taller-codebase-rag` for Module 4 and `taller-research-agents` for Module 5) are deleted first, after the user confirms at that moment. Then:

```bash
cd Final_project/poc_searxng
railway init --name <project-name>              # the final project's Railway project
railway add --service searxng
railway link --service searxng
openssl rand -hex 32 | tr -d '\n' | railway variable set SEARXNG_SECRET --stdin --skip-deploys > /dev/null
railway up --service searxng --detach            # builds the Dockerfile; NO public domain

# Q1 + Q2: run the probe inside the container (Railway's egress IP)
railway ssh --service searxng -- python3 /opt/probe/probe.py --base http://127.0.0.1:8080 > results/railway.json

# Q3: private network
railway ssh --service searxng -- wget -qO- http://searxng.railway.internal:8080/healthz

# Q4: memory
railway metrics --service searxng --memory
```

- **A second probe run** about an hour later shows whether blocking changes over time.
- **The cross-service private-network check** (backend → SearXNG) can only be done once the backend exists. The check above confirms the DNS name and the `::` binding.
- **If the POC passes, the `searxng` service is kept** as the production search service. If not, it's deleted and plan B applies.

### Results

*To be filled after the run.*

## 6. Decision

*To be filled after the run: SearXNG kept or plan B, which engines go into the final `settings.yml`, and the measured RAM.*
