"""SQLite adapters: runs + events + outbox rows (atomic), memories, the outbox, the Telegram
update ledger, caches (LLM responses, Wikipedia, search results, pages) and settings. The only
module with SQL; every value is a bound parameter."""

import json
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.errors import RunNotFound
from app.domain.outbox import OutboxMessage, OutboxStatus, iso
from app.domain.ports import Memory
from app.domain.run import ResearchRun, RunEvent, RunStatus, run_from_dict, run_to_dict

from . import database

_UPSERT_RUN = (
    "INSERT INTO runs (id, status, state_json, duration_ms, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET status = excluded.status, state_json = excluded.state_json, "
    "duration_ms = excluded.duration_ms, updated_at = excluded.updated_at"
)
_INSERT_EVENT = "INSERT INTO events (run_id, type, data_json) VALUES (?, ?, ?)"
_GET_RUN = "SELECT state_json FROM runs WHERE id = ?"
_EVENTS_SINCE = "SELECT id, type, data_json FROM events WHERE run_id = ? AND id > ? ORDER BY id"
_RUNS_WITH_STATUS = "SELECT state_json FROM runs WHERE status = ?"
_OLD_RUNS = "SELECT id FROM runs WHERE updated_at < ? AND status IN (?, ?, ?)"
_DELETE_EVENTS = "DELETE FROM events WHERE run_id = ?"
_DELETE_RUN = "DELETE FROM runs WHERE id = ?"
_STATUS_COUNTS = "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
_DURATIONS = (
    "SELECT duration_ms FROM runs WHERE status = ? AND duration_ms IS NOT NULL "
    "ORDER BY updated_at DESC LIMIT ?"
)
_PUT_MEMORY = (
    "INSERT OR REPLACE INTO memories (run_id, question, summary, source_titles_json, created_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
_RECENT_MEMORIES = "SELECT * FROM memories ORDER BY created_at DESC, rowid DESC LIMIT ?"
_DELETE_MEMORIES = "DELETE FROM memories WHERE created_at < ?"
_GET_CACHE = "SELECT response_json FROM llm_cache WHERE key = ?"
_PUT_CACHE = "INSERT OR REPLACE INTO llm_cache (key, response_json) VALUES (?, ?)"
_ALL_CACHE = "SELECT key, response_json FROM llm_cache"
_GET_WIKI = "SELECT response_json, fetched_at FROM wiki_cache WHERE key = ?"
_PUT_WIKI = "INSERT OR REPLACE INTO wiki_cache (key, response_json, fetched_at) VALUES (?, ?, ?)"
_INSERT_OUTBOX = (
    "INSERT INTO outbox (id, run_id, kind, event, target, payload_json, status, attempts, "
    "next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)"
)
_DUE_OUTBOX = (
    "SELECT * FROM outbox WHERE status = ? AND next_attempt_at <= ? "
    "ORDER BY next_attempt_at, created_at LIMIT ?"
)
_OUTBOX_FOR_RUN = "SELECT * FROM outbox WHERE run_id = ? ORDER BY created_at, id"
_OUTBOX_DELIVERED = (
    "UPDATE outbox SET status = ?, attempts = attempts + 1, delivered_at = ?, last_status = ?, "
    "last_error = NULL WHERE id = ?"
)
_OUTBOX_RETRY = (
    "UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ?, last_status = ? "
    "WHERE id = ?"
)
_OUTBOX_DEAD = (
    "UPDATE outbox SET status = ?, attempts = ?, last_error = ?, last_status = ? WHERE id = ?"
)
_OUTBOX_OLD = "DELETE FROM outbox WHERE created_at < ? AND status != ?"
_OUTBOX_COUNTS = (
    "SELECT status, COUNT(*) AS n, MIN(created_at) AS oldest FROM outbox GROUP BY status"
)
_LEDGER_INSERT = "INSERT OR IGNORE INTO telegram_updates (update_id, received_at) VALUES (?, ?)"
_LEDGER_OLD = "DELETE FROM telegram_updates WHERE received_at < ?"
_GET_SEARCH = "SELECT response_json, fetched_at FROM search_cache WHERE key = ?"
_PUT_SEARCH = (
    "INSERT OR REPLACE INTO search_cache (key, response_json, fetched_at) VALUES (?, ?, ?)"
)
_GET_PAGE = "SELECT url, text, media_type, fetched_at FROM page_cache WHERE url_hash = ?"
_PUT_PAGE = (
    "INSERT OR REPLACE INTO page_cache (url_hash, url, text, media_type, fetched_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
_GET_SETTING = "SELECT value FROM settings WHERE name = ?"
_PUT_SETTING = "INSERT OR IGNORE INTO settings (name, value) VALUES (?, ?)"


class _Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        database.init(path)

    def _conn(self):
        return database.connect(self._path)


class SqliteRunRepository(_Store):
    def create(self, run: ResearchRun) -> None:
        self.save(run)

    def save(self, run: ResearchRun) -> None:
        """Run state, its pending events and its pending outbox messages in ONE transaction:
        the SSE stream, the stored state and the notifications never disagree."""
        with self._lock, self._conn() as conn:
            events = run.pop_events()
            messages = run.pop_outbox()
            conn.execute(
                _UPSERT_RUN,
                (
                    run.id,
                    run.status.value,
                    json.dumps(run_to_dict(run)),
                    run.duration_ms,
                    run.created_at,
                    run.updated_at,
                ),
            )
            for event in events:
                cursor = conn.execute(_INSERT_EVENT, (run.id, event.type, json.dumps(event.data)))
                event.id = cursor.lastrowid
            for m in messages:  # BACKEND_PLAN §13: notifications commit with the state
                conn.execute(
                    _INSERT_OUTBOX,
                    (
                        m.id,
                        m.run_id,
                        m.kind,
                        m.event,
                        m.target,
                        json.dumps(m.payload),
                        m.status.value,
                        m.next_attempt_at,
                        m.created_at,
                    ),
                )

    def get(self, run_id: str) -> ResearchRun:
        with self._conn() as conn:
            row = conn.execute(_GET_RUN, (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return run_from_dict(json.loads(row["state_json"]))

    def events_since(self, run_id: str, after_id: int) -> list[RunEvent]:
        with self._conn() as conn:
            rows = conn.execute(_EVENTS_SINCE, (run_id, after_id)).fetchall()
        return [RunEvent(r["type"], json.loads(r["data_json"]), r["id"]) for r in rows]

    def runs_in(self, statuses: set[RunStatus]) -> list[ResearchRun]:
        with self._conn() as conn:
            rows = [
                r for s in sorted(statuses) for r in conn.execute(_RUNS_WITH_STATUS, (s.value,))
            ]
        return [run_from_dict(json.loads(r["state_json"])) for r in rows]

    def delete_older_than(self, cutoff_iso: str) -> list[str]:
        """Retention: finished runs (and their events) not updated since the cutoff.
        Returns the deleted ids, so their evidence scopes can be removed too (v1.1)."""
        done = (RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value)
        with self._lock, self._conn() as conn:
            ids = [r["id"] for r in conn.execute(_OLD_RUNS, (cutoff_iso, *done))]
            for run_id in ids:
                conn.execute(_DELETE_EVENTS, (run_id,))
                conn.execute(_DELETE_RUN, (run_id,))
        return ids

    def status_counts(self) -> dict[str, int]:
        with self._conn() as conn:
            return {r["status"]: r["n"] for r in conn.execute(_STATUS_COUNTS)}

    def recent_durations_ms(self, limit: int) -> list[float]:
        with self._conn() as conn:
            rows = conn.execute(_DURATIONS, (RunStatus.COMPLETED.value, limit)).fetchall()
        return [float(r["duration_ms"]) for r in rows]


class SqliteMemoryRepository(_Store):
    def record(self, memory: Memory) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                _PUT_MEMORY,
                (
                    memory.run_id,
                    memory.question,
                    memory.summary,
                    json.dumps(memory.reference_titles),
                    memory.created_at,
                ),
            )

    def recent(self, limit: int) -> list[Memory]:
        with self._conn() as conn:
            rows = conn.execute(_RECENT_MEMORIES, (limit,)).fetchall()
        return [
            Memory(
                r["run_id"],
                r["question"],
                r["summary"],
                json.loads(r["source_titles_json"]),
                r["created_at"],
            )
            for r in rows
        ]

    def delete_older_than(self, cutoff_iso: str) -> int:
        with self._lock, self._conn() as conn:
            return conn.execute(_DELETE_MEMORIES, (cutoff_iso,)).rowcount


class SqliteResponseCache(_Store):
    def get(self, key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(_GET_CACHE, (key,)).fetchone()
        return json.loads(row["response_json"]) if row else None

    def put(self, key: str, value: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_PUT_CACHE, (key, json.dumps(value)))

    def export(self) -> dict[str, dict]:
        with self._conn() as conn:
            rows = conn.execute(_ALL_CACHE).fetchall()
        return {r["key"]: json.loads(r["response_json"]) for r in rows}

    def seed(self, entries: dict[str, dict]) -> int:
        with self._lock, self._conn() as conn:
            conn.executemany(_PUT_CACHE, [(k, json.dumps(v)) for k, v in entries.items()])
        return len(entries)


class SqliteWikiCache(_Store):
    """Wikipedia responses by request key, with a time-to-live."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        super().__init__(path)
        self._clock = clock

    def get(self, key: str, max_age_s: float) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(_GET_WIKI, (key,)).fetchone()
        if row is None or self._clock() - row["fetched_at"] > max_age_s:
            return None
        return json.loads(row["response_json"])

    def put(self, key: str, value: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_PUT_WIKI, (key, json.dumps(value), self._clock()))


class SqliteSettings(_Store):
    def prompt_secret(self, configured: str | None = None) -> bytes:
        """PROMPT_SECRET from the environment, else one generated at first start and kept
        in the database (so nonces, the canary and the cache survive restarts)."""
        if configured:
            return configured.encode()
        with self._lock, self._conn() as conn:
            conn.execute(_PUT_SETTING, ("prompt_secret", secrets.token_hex(32)))
            row = conn.execute(_GET_SETTING, ("prompt_secret",)).fetchone()
        return str(row["value"]).encode()


def _message(r) -> OutboxMessage:  # noqa: ANN001 — sqlite3.Row
    return OutboxMessage(
        r["id"],
        r["run_id"],
        r["kind"],
        r["event"],
        r["target"],
        json.loads(r["payload_json"]),
        OutboxStatus(r["status"]),
        r["attempts"],
        r["next_attempt_at"],
        r["last_status"],
        r["last_error"],
        r["created_at"],
        r["delivered_at"],
    )


class SqliteOutboxRepository(_Store):
    def add(self, message: OutboxMessage) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                _INSERT_OUTBOX,
                (
                    message.id,
                    message.run_id,
                    message.kind,
                    message.event,
                    message.target,
                    json.dumps(message.payload),
                    message.status.value,
                    message.next_attempt_at,
                    message.created_at,
                ),
            )

    def due(self, now: datetime, limit: int) -> list[OutboxMessage]:
        with self._conn() as conn:
            rows = conn.execute(_DUE_OUTBOX, (OutboxStatus.PENDING.value, iso(now), limit))
            return [_message(r) for r in rows.fetchall()]

    def mark_delivered(self, message_id: str, at: datetime, status: int | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                _OUTBOX_DELIVERED, (OutboxStatus.DELIVERED.value, iso(at), status, message_id)
            )

    def mark_retry(
        self, message_id: str, attempts: int, next_at: str, error: str, status: int | None = None
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_OUTBOX_RETRY, (attempts, next_at, error, status, message_id))

    def mark_dead(
        self, message_id: str, attempts: int, error: str, status: int | None = None
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                _OUTBOX_DEAD, (OutboxStatus.DEAD.value, attempts, error, status, message_id)
            )

    def for_run(self, run_id: str) -> list[OutboxMessage]:
        with self._conn() as conn:
            return [_message(r) for r in conn.execute(_OUTBOX_FOR_RUN, (run_id,)).fetchall()]

    def delete_older_than(self, cutoff_iso: str) -> int:
        with self._lock, self._conn() as conn:
            return conn.execute(_OUTBOX_OLD, (cutoff_iso, OutboxStatus.PENDING.value)).rowcount

    def counts(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = {r["status"]: r for r in conn.execute(_OUTBOX_COUNTS).fetchall()}
        pending = rows.get(OutboxStatus.PENDING.value)
        oldest = 0.0
        if pending is not None:
            age = datetime.now(UTC) - datetime.fromisoformat(pending["oldest"])
            oldest = max(0.0, age.total_seconds())
        return {
            "pending": float(pending["n"]) if pending is not None else 0.0,
            "dead": float(rows[OutboxStatus.DEAD.value]["n"])
            if OutboxStatus.DEAD.value in rows
            else 0.0,
            "oldest_pending_s": oldest,
        }


class SqliteUpdateLedger(_Store):
    def seen(self, update_id: int) -> bool:
        """Atomic insert-or-ignore: True when the update was already received (duplicate)."""
        with self._lock, self._conn() as conn:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            return conn.execute(_LEDGER_INSERT, (update_id, now)).rowcount == 0

    def forget_older_than(self, cutoff_iso: str) -> int:
        with self._lock, self._conn() as conn:
            return conn.execute(_LEDGER_OLD, (cutoff_iso,)).rowcount


class SqliteSearchCache(_Store):
    """Search results by normalized-query key, with a time-to-live (SEARXNG)."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        super().__init__(path)
        self._clock = clock

    def get(self, key: str, max_age_s: float) -> list[dict] | None:
        with self._conn() as conn:
            row = conn.execute(_GET_SEARCH, (key,)).fetchone()
        if row is None or self._clock() - row["fetched_at"] > max_age_s:
            return None
        return json.loads(row["response_json"])

    def put(self, key: str, value: list[dict]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_PUT_SEARCH, (key, json.dumps(value), self._clock()))


class SqlitePageCache(_Store):
    """Fetched pages by URL hash, with a time-to-live (SEARXNG)."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time) -> None:
        super().__init__(path)
        self._clock = clock

    def get(self, url_hash: str, max_age_s: float) -> tuple[str, str, str] | None:
        with self._conn() as conn:
            row = conn.execute(_GET_PAGE, (url_hash,)).fetchone()
        if row is None or self._clock() - row["fetched_at"] > max_age_s:
            return None
        return row["url"], row["text"], row["media_type"]

    def put(self, url_hash: str, url: str, text: str, media_type: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_PUT_PAGE, (url_hash, url, text, media_type, self._clock()))
