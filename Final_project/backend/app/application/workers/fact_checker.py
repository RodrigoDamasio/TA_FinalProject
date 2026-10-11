"""Fact-Checker (REVIEWER_PLAN §3.3): checks each cited sentence of the latest draft against
the run's evidence, which it retrieves itself (0 LLM calls), then labels every claim in ONE
structured call. Claims that need no model (unknown citations, no evidence text) are settled
in code, and when nothing is left for the model no call is made. Owner: REVIEWER.

Pipeline: extract_claims → select_claims (≤ 12, round-robin across sections) → passages per
claim (cited, same source, elsewhere) → budget → settle → judge → assemble (miscited, stats,
needs_revision)."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.application.citations import CITATION, clean_citations, normalize_citations
from app.application.prompts import PromptLibrary
from app.application.structured import StructuredCaller
from app.application.tracing import Tracer
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind, evidence_label
from app.domain.ports import LLMClient, Retriever
from app.domain.schemas import ClaimVerdict, FactCheck, FactCheckLLM
from app.domain.transcript import WorkerResult

from .base import WorkerContext, WorkerOutput

MAX_CLAIMS = 12
MIN_CLAIM_CHARS = 20
MAX_CITED_PER_CLAIM = 3
SEARCH_K = 8
SNIPPET_CHARS = 900  # EVIDENCE_SNIPPET_CHARS default (§6); the worker has no settings
CUT_CITED_CHARS = 500  # last budget step: cited passages cut to this length
RENDER_CHARS = 1200  # the Supervisor's context keeps 1,200 characters of each result
SENTENCE_CHARS = 160
REVISION_RATIO = 0.25

NO_DRAFT = "There is no draft to review yet."
NOTHING_TO_CHECK = "No cited sentences to check."
UNKNOWN_NOTE = "cites evidence the run never issued"
UNAVAILABLE_NOTE = "evidence text unavailable"

_REFERENCES = re.compile(r"^(sources|references|bibliography)\b", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
# A sentence ends at . ! ? (plus any citation markers right after it) followed by whitespace
# and an upper-case letter, digit, quote, ( or [. Markers that open the next sentence
# ("… costs. [E2] Next") therefore stay with the sentence they follow.
_BOUNDARY = re.compile(r"[.!?](?:\s*\[E\d{1,4}\])*(?=\s+[A-Z0-9\"'“(\[])")
_ABBREVIATIONS = ("e.g.", "i.e.", "vs.", "etc.", "fig.", "no.", "u.s.")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")


# ---- step 1: extraction -------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimCandidate:
    text: str  # the sentence as written (normalized citations)
    plain: str  # without markers and emphasis: the retrieval query
    section: str | None  # None = the summary before the first section
    position: int  # order in the draft
    cited: tuple[int, ...]  # issued evidence numbers, sorted
    unknown: tuple[int, ...]  # numbers the run never issued


def paragraphs(draft: str) -> list[tuple[str | None, str, bool]]:
    """(section, text, is_table_row) for every paragraph, list item and table row; the title,
    fenced code and a Sources/References section are skipped."""
    out: list[tuple[str | None, str, bool]] = []
    section: str | None = None
    skipping = in_code = False
    buffer: list[str] = []

    def flush() -> None:
        if buffer and not skipping:
            out.append((section, " ".join(buffer), False))
        buffer.clear()

    for raw in draft.splitlines():
        if _FENCE.match(raw):
            flush()
            in_code = not in_code
            continue
        line = raw.strip()
        if in_code:
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            heading = line[level:].strip()
            if level >= 2:
                skipping = bool(_REFERENCES.match(heading))
                section = heading or section
            continue
        line = line.lstrip("> ").strip() if line.startswith(">") else line
        if not line:
            flush()
        elif line.startswith("|"):
            flush()
            if not _TABLE_SEPARATOR.match(line) and not skipping:
                cells = [c.strip() for c in line.strip("|").split("|")]
                out.append((section, " · ".join(c for c in cells if c), True))
        elif _LIST_ITEM.match(line):
            flush()
            buffer.append(_LIST_ITEM.sub("", line, count=1))
        else:
            buffer.append(line)
    flush()
    return out


def _abbreviation(head: str) -> bool:
    """`head` ends with a protected abbreviation that starts a word ("e.g." but not "piano.")."""
    low = head.lower()
    for abbr in _ABBREVIATIONS:
        if low.endswith(abbr):
            before = low[: -len(abbr)]
            return not before or not before[-1].isalnum()
    return False


def split_sentences(paragraph: str) -> list[str]:
    sentences, start = [], 0
    for m in _BOUNDARY.finditer(paragraph):
        if _abbreviation(paragraph[start : m.start() + 1]):
            continue
        sentences.append(paragraph[start : m.end()].strip())
        start = m.end()
    sentences.append(paragraph[start:].strip())
    return [s for s in sentences if s]


def _plain(sentence: str) -> str:
    text = " ".join(_EMPHASIS.sub("", CITATION.sub("", sentence)).split())
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)


def extract_claims(draft: str, valid: set[int]) -> list[ClaimCandidate]:
    """Every cited sentence of the draft (cites issued or unknown evidence, ≥ 20 characters),
    in document order, duplicates (same plain text) dropped."""
    out: list[ClaimCandidate] = []
    seen: set[str] = set()
    for section, paragraph, table_row in paragraphs(normalize_citations(draft)):
        for sentence in [paragraph] if table_row else split_sentences(paragraph):
            _, cited, unknown = clean_citations(sentence, valid)
            plain = _plain(sentence)
            key = plain.lower()
            if not (cited or unknown) or len(plain) < MIN_CLAIM_CHARS or key in seen:
                continue
            seen.add(key)
            unknown_ns = tuple(dict.fromkeys(unknown))
            out.append(ClaimCandidate(sentence, plain, section, len(out), cited, unknown_ns))
    return out


# ---- step 2: selection --------------------------------------------------------------------


def select_claims(
    candidates: list[ClaimCandidate], limit: int = MAX_CLAIMS
) -> list[ClaimCandidate]:
    """All claims when they fit; otherwise round-robin across sections (so every section is
    checked), preferring sentences with a digit, then document order. Returned in document
    order."""
    if len(candidates) <= limit:
        return list(candidates)
    groups: dict[str | None, list[ClaimCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.section, []).append(c)
    queues = [
        sorted(g, key=lambda c: (not any(ch.isdigit() for ch in c.plain), c.position))
        for g in groups.values()
    ]
    chosen: list[ClaimCandidate] = []
    while len(chosen) < limit:
        for queue in queues:
            if queue and len(chosen) < limit:
                chosen.append(queue.pop(0))
    return sorted(chosen, key=lambda c: c.position)


# ---- step 3: passages ---------------------------------------------------------------------


@dataclass
class Claim:
    id: str  # "C1"
    candidate: ClaimCandidate
    cited: list[int] = field(default_factory=list)  # loaded cited passages (≤ 3)
    same: list[int] = field(default_factory=list)  # same source, other passage (≤ 1)
    elsewhere: list[int] = field(default_factory=list)  # elsewhere in the evidence (≤ 1)
    settled: ClaimVerdict | None = None  # decided in code (origin "check")

    @property
    def shown(self) -> list[int]:
        return sorted({*self.cited, *self.same, *self.elsewhere})


@dataclass
class Passages:
    claims: list[Claim]
    texts: dict[int, str]  # n → passage text shown to the model
    degraded: bool = False


class EvidenceUnavailable(Exception):
    """Retriever.get failed: the Fact-Checker cannot run."""


def gather(
    claims: list[Claim],
    evidence: list[EvidenceRef],
    retriever: Retriever,
    run_id: str,
    snippet_chars: int = SNIPPET_CHARS,
) -> Passages:
    """Loads the cited passages (one Retriever.get) and finds, per claim, one more passage of
    the same source and one elsewhere in the evidence. Only numbered evidence is kept."""
    by_n = {r.n: r for r in evidence}
    numbered = {r.chunk_id: r.n for r in evidence}
    wanted = list(
        dict.fromkeys(
            by_n[n].chunk_id for c in claims for n in c.candidate.cited[:MAX_CITED_PER_CLAIM]
        )
    )
    try:
        loaded = {chunk.id: chunk for chunk in retriever.get(wanted)} if wanted else {}
    except Exception as err:  # noqa: BLE001 — any store error means "no evidence text"
        raise EvidenceUnavailable from err
    texts: dict[int, str] = {}
    keys: dict[int, str] = {}

    def keep(chunk: EvidenceChunk) -> int:
        n = numbered[chunk.id]
        texts.setdefault(n, chunk.text[:snippet_chars])
        keys.setdefault(n, chunk.source_key)
        return n

    for c in claims:
        c.cited = [
            keep(loaded[by_n[n].chunk_id])
            for n in c.candidate.cited[:MAX_CITED_PER_CLAIM]
            if by_n[n].chunk_id in loaded
        ]
    scopes = list(dict.fromkeys([f"run:{run_id}", *(ch.scope for ch in loaded.values())]))
    degraded = False

    def first_new(query: str, claim: Claim, source_keys: list[str] | None) -> list[int]:
        taken = {*claim.candidate.cited, *claim.same}
        for hit in retriever.search(query, scopes, SEARCH_K, source_keys):
            n = numbered.get(hit.chunk.id)
            if n is not None and n not in taken:
                return [keep(hit.chunk)]
        return []

    for c in claims:
        if not c.candidate.cited:
            continue  # cites only unknown evidence: settled in code, nothing to search
        try:
            sources = list(dict.fromkeys(keys[n] for n in c.cited))
            if sources:
                c.same = first_new(c.candidate.plain, c, sources)
            c.elsewhere = first_new(c.candidate.plain, c, None)
        except Exception:  # noqa: BLE001 — index warming up, store error: cited only
            degraded = True
            for other in claims:
                other.same, other.elsewhere = [], []
            break
    return Passages(claims, texts, degraded)


def fit_budget(passages: Passages, budget: int) -> None:
    """Total passage text ≤ budget: drop the "elsewhere" passages (last claims first), then
    the "same source" ones, then cut cited passages to CUT_CITED_CHARS."""

    def total() -> int:
        shown = {n for c in passages.claims for n in c.shown}
        return sum(len(passages.texts[n]) for n in shown)

    for attr in ("elsewhere", "same"):
        for c in reversed(passages.claims):
            if total() <= budget:
                return
            setattr(c, attr, [])
    if total() > budget:
        for n, text in passages.texts.items():
            passages.texts[n] = text[:CUT_CITED_CHARS]


# ---- step 4: settle in code ---------------------------------------------------------------


def settle(claims: list[Claim]) -> None:
    for c in claims:
        if not c.candidate.cited:
            c.settled = ClaimVerdict(claim=c.id, status="unsupported", note=UNKNOWN_NOTE)
        elif not c.shown:
            c.settled = ClaimVerdict(claim=c.id, status="unsupported", note=UNAVAILABLE_NOTE)


# ---- step 5: the judging call -------------------------------------------------------------


def claim_ids_check(expected: list[str]) -> Callable[[Any], list[str]]:
    """The StructuredCaller rule: one verdict per claim id, no unknown or repeated id."""

    def check(value: FactCheck) -> list[str]:
        got = [c.claim for c in value.claims]
        errors = [f"{i} is not one of the claim ids" for i in got if i not in expected]
        errors += [f"{i} appears more than once" for i in dict.fromkeys(got) if got.count(i) > 1]
        errors += [f"missing a verdict for claim {i}" for i in expected if i not in got]
        return errors

    return check


def source_of(ref: EvidenceRef) -> str:
    if ref.source_kind is SourceKind.DOCUMENTS or not ref.url:
        name = ref.document_name or ref.title
        return f"{name}, p. {ref.page}" if ref.page else name
    host = urlparse(ref.url).hostname or ref.url
    return host.removeprefix("www.")


def claim_line(c: Claim) -> str:
    cites = ", ".join(evidence_label(n) for n in c.candidate.cited)
    against = ", ".join(evidence_label(n) for n in c.shown)
    return (
        f"{c.id} [cites {cites}] ({c.candidate.section or 'Summary'}) {c.candidate.text} "
        f"→ check against: {against}"
    )


# ---- step 6: assembly and rendering -------------------------------------------------------


def _label_ns(labels: list[str]) -> list[int]:
    return [int(label[1:]) for label in labels]


def assemble(claims: list[Claim], judged: dict[str, ClaimVerdict]) -> dict[str, Any]:
    """Joins the verdicts with the claims by id. Claims with no verdict are `skipped`."""
    items: list[dict[str, Any]] = []
    for c in claims:
        verdict = c.settled or judged.get(c.id)
        if verdict is None:
            continue
        shown = {evidence_label(n) for n in c.shown}
        ids = [i for i in verdict.evidence_ids if c.settled or i in shown]
        cited = [evidence_label(n) for n in c.candidate.cited]
        miscited = verdict.status == "supported" and bool(ids) and not set(ids) & set(cited)
        items.append(
            {
                "claim": c.id,
                "section": c.candidate.section,
                "sentence": c.candidate.text,
                "cited": cited,
                "unknown": [evidence_label(n) for n in c.candidate.unknown],
                "status": verdict.status,
                "evidence_ids": ids,
                "note": verdict.note,
                "miscited": miscited,
                "origin": "check" if c.settled else "model",
            }
        )
    return {"claims": items}


def fact_stats(items: list[dict[str, Any]], cited_sentences: int, selected: int) -> dict:
    count = {
        s: sum(1 for i in items if i["status"] == s)
        for s in ("supported", "unsupported", "contradicted")
    }
    checked = len(items)
    return {
        "cited_sentences": cited_sentences,
        "checked": checked,
        "skipped": selected - checked,
        **count,
        "miscited": sum(1 for i in items if i["miscited"]),
        "supported_ratio": round(count["supported"] / checked, 3) if checked else 0.0,
    }


def needs_revision(stats: dict[str, Any]) -> bool:
    if stats["contradicted"] > 0:
        return True
    weak = stats["unsupported"] + stats["miscited"]
    return weak / max(1, stats["checked"]) >= REVISION_RATIO


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_fact_check(items: list[dict[str, Any]], stats: dict, revise: bool) -> str:
    """Worst first (contradicted, unsupported, miscited), within RENDER_CHARS."""
    if not stats["cited_sentences"]:
        return NOTHING_TO_CHECK
    head = (
        f"Fact check: {stats['checked']} of {stats['cited_sentences']} cited sentences "
        f"checked: {stats['supported']} supported, {stats['unsupported']} unsupported, "
        f"{stats['contradicted']} contradicted."
    )
    if stats["miscited"]:
        head += f" {stats['miscited']} miscited."
    if stats["skipped"]:
        head += f" {stats['skipped']} skipped."
    head += " Revision needed." if revise else " No revision needed."
    rank = {"contradicted": 0, "unsupported": 1}
    bad = [i for i in items if i["status"] != "supported" or i["miscited"]]
    bad.sort(key=lambda i: rank.get(i["status"], 2))
    lines = [head]
    for n, i in enumerate(bad):
        label = "miscited" if i["status"] == "supported" else i["status"]
        if i["miscited"]:
            note = f"Supported by {', '.join(i['evidence_ids'])}, not {', '.join(i['cited'])}."
        else:
            note = _cut(i["note"], 120)
        line = f'- {label} ({i["section"] or "Summary"}): "{_cut(i["sentence"], SENTENCE_CHARS)}"'
        line += f" {note}" if note else ""
        more = f"- … and {len(bad) - n} more."
        if len("\n".join([*lines, line])) + len(more) + 1 > RENDER_CHARS:
            lines.append(more)
            break
        lines.append(line)
    return "\n".join(lines)


# ---- the worker ---------------------------------------------------------------------------


class FactChecker:
    name = "fact_checker"
    title = "Fact-Checker"
    description = (
        "checks each cited sentence of the latest draft against the evidence it cites and "
        "other passages it retrieves; returns supported/unsupported/contradicted per claim and "
        "needs_revision; can run in parallel with the reviewer"
    )
    needs = frozenset({"writer"})

    def __init__(self, prompts: PromptLibrary, snippet_chars: int = SNIPPET_CHARS) -> None:
        self._prompts, self._snippet = prompts, snippet_chars

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        draft = ctx.results.get("writer")
        if draft is None or draft.failed:
            return WorkerOutput(WorkerResult(self.name, NO_DRAFT, failed=True))
        tracer = Tracer("worker.fact_checker", ctx.run_id)
        candidates = extract_claims(draft.content, {r.n for r in ctx.evidence})
        selected = select_claims(candidates)
        claims = [Claim(f"C{i}", c) for i, c in enumerate(selected, 1)]
        notices: list[tuple[str, str]] = []
        with tracer.span("gather") as span:
            try:
                passages = gather(claims, ctx.evidence, ctx.retriever, ctx.run_id, self._snippet)
            except EvidenceUnavailable:
                span["ok_store"] = False
                text = "The fact check could not run: the evidence store is unavailable."
                return WorkerOutput(WorkerResult(self.name, text, failed=True))
            fit_budget(passages, ctx.context_chars)
            settle(claims)
            span |= {"claims": len(claims), "passages": len(passages.texts)}
        if passages.degraded:
            notices.append(
                (
                    "The fact check used only the cited evidence (the evidence search failed).",
                    "fact_check_degraded",
                )
            )
        judged = self._judge(task, ctx, llm, passages, tracer) if claims else {}
        data = assemble(claims, judged)
        stats = fact_stats(data["claims"], len(candidates), len(claims))
        revise = needs_revision(stats)
        data |= {"stats": stats, "needs_revision": revise}
        ns = sorted({n for i in data["claims"] for n in _label_ns(i["evidence_ids"])})
        content = render_fact_check(data["claims"], stats, revise)
        return WorkerOutput(WorkerResult(self.name, content, data, tuple(ns)), notices)

    def _judge(
        self,
        task: str,
        ctx: WorkerContext,
        llm: LLMClient,
        passages: Passages,
        tracer: Tracer,
    ) -> dict[str, ClaimVerdict]:
        open_claims = [c for c in passages.claims if c.settled is None]
        if not open_claims:
            return {}
        f = ctx.fence
        by_n = {r.n: r for r in ctx.evidence}
        shown = sorted({n for c in open_claims for n in c.shown})
        evidence = "\n\n".join(
            f.wrap(
                passages.texts[n],
                "evidence",
                id=evidence_label(n),
                title=by_n[n].title,
                source=source_of(by_n[n]),
            )
            for n in shown
        )
        user = self._prompts.worker_task(
            "fact_checker_task",
            task=task,
            sentences=f.wrap("\n".join(claim_line(c) for c in open_claims), "draft_claims"),
            evidence=evidence,
        )
        result = StructuredCaller(llm, self._prompts, ctx.model or "").call(
            self._prompts.worker_system(self.name, ctx.canary),
            user,
            FactCheckLLM,
            FactCheck,
            tracer,
            max_output_tokens=3072,
            check=claim_ids_check([c.id for c in open_claims]),
        )
        check = result.value or result.last_valid
        assert check is not None  # noqa: S101 — call() raises when nothing was valid
        expected = {c.id for c in open_claims}
        judged: dict[str, ClaimVerdict] = {}
        for verdict in check.claims:
            if verdict.claim in expected:
                judged.setdefault(verdict.claim, verdict)
        return judged
