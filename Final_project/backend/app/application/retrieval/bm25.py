"""Keyword search: a tokenizer and an incremental BM25 index (Okapi, Lucene IDF), from M4.

RAG_PLAN §3.4 changes: no `codebase` field; documents are grouped by `source_key`; a PROSE
tokenizer mode (English stop words only; code words like `function` or `return` stay, since
they carry meaning in prose); and ScopedBM25 keeps ONE index per scope, so term statistics
never mix across scopes (the fix for M4's cross-codebase leak). Owner: RAG."""

import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
CODE_STOP_WORDS = frozenset(
    "the a an and or of to in on for is are be by it as at with from this that what how "
    "does do which where when who why self return const let var def import export "
    "function class async await new none null true false".split()
)
PROSE_STOP_WORDS = frozenset(
    "the a an and or but of to in on for is are was were be been being by it its as at with "
    "from this that these those what how does do did which where when who whom why than then "
    "so such if not no can could should would will may might must there here their they them "
    "we you your our his her he she i me my".split()
)


def tokenize(text: str, mode: str = "prose") -> list[str]:
    """`processOrder` → process, order, processorder; `max_tokens` → max, tokens,
    max_tokens. Lowercase; stop words and 1-char tokens dropped."""
    stop = PROSE_STOP_WORDS if mode == "prose" else CODE_STOP_WORDS
    tokens: list[str] = []
    for word in _WORD.findall(text):
        parts = [p for piece in word.split("_") for p in _CAMEL.findall(piece)]
        lowered = [p.lower() for p in parts]
        tokens += lowered
        if len(lowered) > 1:
            tokens.append(word.lower())
    return [t for t in tokens if len(t) > 1 and t not in stop]


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: str
    score: float
    matched_terms: list[str]


class BM25Index:
    """One scope's index. Thread-safe; documents can be added and removed incrementally."""

    def __init__(self, k1: float = 1.5, b: float = 0.75, mode: str = "prose") -> None:
        self.k1, self.b, self.mode = k1, b, mode
        self._docs: dict[str, tuple[str, Counter[str], int]] = {}  # id → (source_key, tf, len)
        self._order: dict[str, tuple[str, int]] = {}  # id → run-independent tie-break key
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._by_source: dict[str, set[str]] = defaultdict(set)
        self._total_len = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, chunk_id: str, text: str, source_key: str = "", position: int = 0) -> None:
        tokens = tokenize(text, self.mode)
        with self._lock:
            if chunk_id in self._docs:
                self._remove(chunk_id)
            tf = Counter(tokens)
            self._docs[chunk_id] = (source_key, tf, len(tokens))
            self._order[chunk_id] = (source_key, position)
            self._by_source[source_key].add(chunk_id)
            self._total_len += len(tokens)
            for term in tf:
                self._postings[term].add(chunk_id)

    def remove(self, chunk_id: str) -> None:
        with self._lock:
            self._remove(chunk_id)

    def remove_source(self, source_key: str) -> int:
        with self._lock:
            ids = list(self._by_source.get(source_key, ()))
            for i in ids:
                self._remove(i)
            return len(ids)

    def _remove(self, chunk_id: str) -> None:
        doc = self._docs.pop(chunk_id, None)
        self._order.pop(chunk_id, None)
        if doc is None:
            return
        source_key, tf, length = doc
        self._total_len -= length
        ids = self._by_source.get(source_key)
        if ids is not None:
            ids.discard(chunk_id)
            if not ids:
                del self._by_source[source_key]
        for term in tf:
            posting = self._postings.get(term)
            if posting is not None:
                posting.discard(chunk_id)
                if not posting:
                    del self._postings[term]

    def search(self, query: str, n: int, source_keys: list[str] | None = None) -> list[KeywordHit]:
        terms = list(dict.fromkeys(tokenize(query, self.mode)))
        allowed = set(source_keys) if source_keys is not None else None
        with self._lock:
            total = len(self._docs)
            if not total or not terms or n <= 0:
                return []
            avg_len = self._total_len / total
            scores: dict[str, float] = defaultdict(float)
            matched: dict[str, list[str]] = defaultdict(list)
            for term in terms:
                ids = self._postings.get(term, set())
                if not ids:
                    continue
                idf = math.log(1 + (total - len(ids) + 0.5) / (len(ids) + 0.5))
                for chunk_id in ids:
                    source_key, tf, length = self._docs[chunk_id]
                    if allowed is not None and source_key not in allowed:
                        continue
                    f = tf[term]
                    norm = (
                        f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / avg_len))
                    )
                    scores[chunk_id] += idf * norm
                    matched[chunk_id].append(term)
        # Ties break on (source_key, position), never on the chunk id: ids contain the run's
        # scope, so two identical runs must rank identically (stable LLM cache keys).
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], self._order[kv[0]], kv[0]))[:n]
        return [KeywordHit(i, s, matched[i]) for i, s in ranked]


Loader = Callable[[str], Iterable[tuple[str, str, str, int]]]  # scope → (id, text, key, position)


class ScopedBM25:
    """One BM25Index per scope, built lazily from the vector store (the source of truth).
    Pinned scopes (demo, memory) are never evicted; others live in an LRU."""

    def __init__(self, loader: Loader, pinned: tuple[str, ...] = ("demo", "memory"),
                 capacity: int = 16) -> None:  # fmt: skip
        self._loader, self._pinned, self._capacity = loader, set(pinned), capacity
        self._pinned_indexes: dict[str, BM25Index] = {}
        self._lru: OrderedDict[str, BM25Index] = OrderedDict()
        self._lock = threading.Lock()

    def index(self, scope: str) -> BM25Index:
        with self._lock:
            found = self._pinned_indexes.get(scope) or self._lru.get(scope)
            if found is not None:
                if scope in self._lru:
                    self._lru.move_to_end(scope)
                return found
            built = BM25Index()
            for chunk_id, text, source_key, position in self._loader(scope):
                built.add(chunk_id, text, source_key, position)
            if scope in self._pinned:
                self._pinned_indexes[scope] = built
            else:
                self._lru[scope] = built
                while len(self._lru) > self._capacity:
                    self._lru.popitem(last=False)
            return built

    def loaded(self, scope: str) -> bool:
        with self._lock:
            return scope in self._pinned_indexes or scope in self._lru

    def evict(self, scope: str) -> None:
        with self._lock:
            self._pinned_indexes.pop(scope, None)
            self._lru.pop(scope, None)

    def search(
        self, scope: str, query: str, n: int, source_keys: list[str] | None = None
    ) -> list[KeywordHit]:
        return self.index(scope).search(query, n, source_keys)
