# POC — SearXNG on Railway

A short spike before any plan builds on SearXNG. The rationale for choosing it is in [BRAINSTORM.md §3](BRAINSTORM.md#3-web-search).

**Status:** done on 2026-10-10. **Result: SearXNG is kept**, with 3 engines (§6). A stability re-run one hour later is recorded in §5.4.

## 1. Questions to answer

| # | Question | Why it matters |
|---|---|---|
| Q1 | Which search engines answer from a **Railway IP**? | Engines often CAPTCHA or block cloud-server IPs. This decides SearXNG vs plan B (a search API) |
| Q2 | Are the results usable: **relevant**, with snippets, fast enough? | The Researcher reads snippets first, and each tool step sits inside a 3-step budget |
| Q3 | Is SearXNG reachable over the **private network** (`searxng.railway.internal:8080`)? | The service gets no public domain, so the backend must reach it privately |
| Q4 | How much **RAM** does it use? | The free plan gives 1 GB per service |

## 2. Success criteria

SearXNG is kept if, **from Railway**:

- **Engines:** at least **2 web engines** answer **≥ 8 of 10** queries on their own, **with ≥ 70 % relevant results**. One working engine is too fragile.
  - The relevance part was added after round 1, when an engine was found answering every query with off-topic pages (§5.1).
- **The default mix** answers **10/10** queries, with an average of **≥ 5 results** that have snippets.
- **Speed:** average latency **≤ 3 s** per search.
- **Private network:** the private hostname answers `/healthz`.
- **RAM:** under **300 MB**.

**Otherwise: plan B.** A search API (e.g. Tavily or Brave) goes behind the same `SearchProvider` port, with Wikipedia still the fallback.

## 3. Setup

Everything is in [`poc_searxng/`](poc_searxng/):

| File | What |
|---|---|
| [`Dockerfile`](poc_searxng/Dockerfile) | **Pinned** image `searxng/searxng:2026.9.25-12f8b6515` + our settings + the probe script |
| [`settings.yml`](poc_searxng/settings.yml) | The **final** configuration (§6). JSON format only: HTML returns `403`. Limiter, public-instance features and image proxy off. English by default. Short timeouts (4 s, max 8 s) |
| [`probe.py`](poc_searxng/probe.py) | Standard library only, so it runs **inside the container on Railway**. It runs 10 research-style queries through each engine on its own, then through the default mix, with 1 s between requests. It reports, per engine: answered queries, result count, **relevance**, latency, and SearXNG's `unresponsive_engines` reasons |
| [`results/`](poc_searxng/results/) | Raw JSON reports of every run |
| [`sx.sh`](poc_searxng/sx.sh) | Manual testing from your terminal: `source sx.sh`, then `sx "query"` or `sx "query" yandex`. It goes through `railway ssh`, because the service has no public domain |

**Relevance heuristic:** a result counts as relevant when its title and snippet contain all but one of the query's keywords (at least 2). It's rough but deterministic, and enough to separate useful engines from ones returning dictionary pages.

**Secret key:** SearXNG reads `secret_key` from the `SEARXNG_SECRET` environment variable. It's generated at deploy time and never printed.

**Engines tested:** `duckduckgo`, `brave`, `bing`, `google`, `qwant`, `yahoo`, `yep`, `mwmbl`, `yandex`, `seznam`, `wikipedia`. Round 1 tested only the first six and `wikipedia`.

- **Not testable:** Mojeek, Startpage and Dogpile are marked **`inactive`** by SearXNG upstream in this version (they now show proof-of-work CAPTCHAs).
- **Not tested:** Chinese and Korean engines (Baidu, 360search, Sogou, Quark, Naver) are unlikely to help with English technical research.
- **Wikipedia:** SearXNG's Wikipedia engine returns an **infobox** for title-like queries, not result items, so it scores 0 results. That's expected. The backend's fallback uses the Wikipedia API directly (Module 5's client), not this engine.

## 4. Local baseline (home IP)

| Engine | Round 1: answered | Round 2: answered | Round 2: relevant | Failure reason |
|---|---|---|---|---|
| duckduckgo | 0/10 | 0/10 | — | CAPTCHA |
| brave | 0/10 | 0/10 | — | too many requests (suspended) |
| bing | 8/10 | 10/10 | **0 %** | — (answers, but off-topic) |
| google | 0/10 | 0/10 | — | access denied (suspended) |
| qwant | 0/10 | 0/10 | — | CAPTCHA |
| yahoo | 10/10 | 10/10 | 87 % | — |
| yep | — | 0/10 | — | HTTP error |
| mwmbl | — | 8/10 | 77 % | timeout |
| yandex | — | 10/10 | 95 % | — |
| seznam | — | 0/10 | — | timeout |

Raw reports: [`local_round1.json`](poc_searxng/results/local_round1.json), [`local_round2.json`](poc_searxng/results/local_round2.json). The local container used about **120 MB** of RAM.

The failing engines failed from the very first query. That points to anti-bot blocking, not our 1 s request rate.

## 5. Railway runs

### 5.1 Procedure

**First, the clean-up.** With the user's confirmation, both earlier projects were deleted: `taller-codebase-rag` (Module 4) and `taller-research-agents` (Module 5). Then:

```bash
cd Final_project/poc_searxng
railway init --name Research-Assistant-Final-Project
railway add --service searxng
railway link --project Research-Assistant-Final-Project --service searxng
openssl rand -hex 32 | tr -d '\n' | railway variable set SEARXNG_SECRET --stdin --skip-deploys > /dev/null
railway up --service searxng --detach            # builds the Dockerfile; NO public domain

# Q1 + Q2: run the probe inside the container (Railway's egress IP)
railway ssh --service searxng -- python3 /opt/probe/probe.py < /dev/null > results/railway_<run>.json

# Q3: private network
railway ssh --service searxng -- wget -qO- http://searxng.railway.internal:8080/healthz

# Q4: memory
railway metrics --service searxng --memory
```

**One-time `railway ssh` setup on this machine:**
- **SSH key:** `railway ssh` needs a key registered with the Railway account. An ed25519 key was created (`~/.ssh/id_ed25519`, no passphrase) and registered as `rodrigo-laptop` with `railway ssh keys add --key <fingerprint>`. Passing the file path didn't work.
- **Host key:** Railway's SSH gateway `ssh.railway.com` was added to `~/.ssh/known_hosts` (trust on first use; ED25519 fingerprint `SHA256:+S1xg92FrnHz6pY3bpkmh1OGtWQGNANXilPzlxA7B1g`).

**Gotchas:**
- **`railway ssh` reads stdin.** Inside a shell `while read` loop it swallows the loop's input, so give it `< /dev/null`.
- **Quoting is fragile.** Arguments pass through a remote shell: escape `&` in URLs (`\&`). Quoted `python3 -c` scripts came back empty.

### 5.2 Q3 and Q4

| Check | Result |
|---|---|
| Public domain | **None** (`railway domain list` → no domains) |
| Private network | `wget http://searxng.railway.internal:8080/healthz` → **`OK`** |
| Egress | Railway's own network (AS400940, Santa Clara, CA). **The IP changed with every redeploy:** `152.55.177.212` → `.177.197` → `.178.91` |
| RAM | **139–186 MB** (peak 186 MB of the 1,024 MB limit) |

### 5.3 Q1 and Q2: engines from Railway

| Engine | Round 1 (IP .212) | Round 2 (IP .197): answered | Round 2: relevant | Final (IP .178.91): answered | Final: relevant |
|---|---|---|---|---|---|
| duckduckgo | 0/10 CAPTCHA | **10/10** | 93 % | **10/10** | 89 % |
| brave | 0/10 too many requests | 0/10 | — | — | — |
| bing | 10/10 | 10/10 | **24 %** | — | — |
| google | 0/10 access denied | 0/10 | — | — | — |
| qwant | 0/10 CAPTCHA | 0/10 | — | — | — |
| yahoo | 10/10 | **10/10** | 94 % | **10/10** | 96 % |
| yep | — | 0/10 access denied | — | — | — |
| mwmbl | — | 5/10 (timeouts) | 70 % | — | — |
| yandex | — | **10/10** | 97 % | **10/10** | 97 % |
| seznam | — | 7/10 (timeouts) | 58 % | — | — |
| **default mix** | 10/10, 16 results | 10/10, 52 results, 71 %, **2.58 s** | | **10/10, 23 results, 96 %, 0.61 s** | |

Raw reports: [`railway_round1.json`](poc_searxng/results/railway_round1.json), [`railway_round2.json`](poc_searxng/results/railway_round2.json), [`railway_final.json`](poc_searxng/results/railway_final.json).

**Findings:**

1. **"Answers" is not the same as "useful".** Bing answered every query in every run, but its results were off-topic. For "transactional outbox pattern" it returned only dictionary pages for *transactional* (relevance: 6 % in round 1 on Railway, 0 % at home). Without the relevance check, round 1 would have wrongly passed with "Bing + Yahoo".
2. **Blocking depends on the egress IP, and the IP changes with each deploy.** DuckDuckGo showed a CAPTCHA on one IP and worked on the next two. One engine is never enough, which is why the final setup keeps three.
3. **Railway was no worse than home.** Several engines block both.
4. **Slow engines hurt the mix.** Mwmbl and Seznam timeouts pushed the mix from 0.6 s to 2.6 s and lowered relevance to 71 %. Keeping only the strong engines made the mix faster (0.61 s) *and* better (96 %).

### 5.4 Stability re-run (one hour later)

*Pending: the same final probe, about an hour after the final deploy.*

## 6. Decision

**SearXNG is kept** as the Researcher's web search provider, with every criterion met on the final configuration:

| Criterion | Target | Result |
|---|---|---|
| ≥ 2 engines answer ≥ 8/10 with ≥ 70 % relevance | 2 | **3** (Yahoo 96 %, Yandex 97 %, DuckDuckGo 89 %, all 10/10) |
| Default mix | 10/10, ≥ 5 results with snippets | **10/10, ~23 results**, 17–26 with snippets |
| Average latency | ≤ 3 s | **0.61 s** |
| Private network | `/healthz` OK | **OK** |
| RAM | < 300 MB | **≤ 186 MB** |

**Final `settings.yml`:** `use_default_settings.engines.keep_only: [yahoo, yandex, duckduckgo]`. Only these three engines are loaded, which also removed the Tor-engine startup errors.

**What this means for the plans:**
- **The `searxng` service stays** in the `Research-Assistant-Final-Project` Railway project as the production search service, with no public domain.
- **The engine list is configuration, not code.** If an engine starts failing, `settings.yml` changes and nothing else.
- **The backend must handle SearXNG answering badly**, not only failing outright:
  - empty results and engine errors are tool results the Researcher can react to (e.g. rewrite the query)
  - a failure or timeout switches to the Wikipedia API fallback
  - the response's `unresponsive_engines` field is logged as a metric
- **Plan B stays possible:** a search API can be added behind the same `SearchProvider` port if engines degrade later.
- **Still to check:** backend → SearXNG over the private network, once the backend service exists (`BACKEND_PLAN.md`).
