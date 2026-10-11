# Query the SearXNG service on Railway from your terminal.
#   source sx.sh
#   sx "transactional outbox pattern"          # all engines
#   sx "fastapi server sent events" yandex     # one engine: yahoo, yandex or duckduckgo
# Run from this folder (it is linked to the Railway project).
sx() {
  local query="$1"
  [ -n "$2" ] && query="!$2 $1"   # SearXNG "bang": restrict to one engine
  local q
  q=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote_plus(sys.argv[1]))' "$query")
  # Only one "&" may be passed (escaped) through railway ssh's remote shell.
  railway ssh --service searxng -- wget -qO- "http://127.0.0.1:8080/search?q=${q}\&format=json" 2>/dev/null </dev/null \
    | sed -n '/^{/,$p' \
    | python3 -c '
import sys, json
d = json.load(sys.stdin)
for i, r in enumerate(d["results"][:10], 1):
    print(f"{i:2}. [{",".join(r["engines"])}] {r["title"]}")
    print(f"    {r["url"]}")
print(f"{len(d["results"])} results · unresponsive engines: {d["unresponsive_engines"] or "none"}")'
}
