"""RunService ("the Run service", PLAN §3): the use cases behind every channel (web, API,
Telegram): submit a question, read it, approve or reject its answer, and the housekeeping
(approval expiry, retention, restart recovery). No LLM calls. Every check that can reject a
request for free runs before anything is queued (BACKEND_PLAN §13)."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.depth import PROFILES, Depth
from app.domain.errors import (
    ConversationBusy,
    ConversationFull,
    ConversationNotFound,
    FetchRefused,
    InputTooLarge,
    InvalidCallbackUrl,
    InvalidField,
    QueueFull,
)
from app.domain.evidence import REQUESTABLE_SOURCES, SourceKind
from app.domain.outbox import EVENT_ACCEPTED, Channel, OutboxMessage, Subscriber
from app.domain.ports import Indexer, JobRunner, MemoryRepository, RunRepository
from app.domain.run import ResearchRun, RunStatus

from .security import InputValidator
from .supervisor import Supervisor

logger = logging.getLogger("app.runs")


@dataclass(frozen=True)
class SubmitCommand:
    """RunService.submit input (BACKEND_PLAN §13). Built by every channel adapter."""

    question: str
    depth: Depth = Depth.DETAILED
    sources: frozenset[SourceKind] = REQUESTABLE_SOURCES
    max_iterations: int | None = None  # None → the depth's default
    require_approval: bool = False  # forced False unless channel == web
    use_memory: bool = True
    workspace_id: str | None = None
    channel: Channel = Channel.WEB
    callback_url: str | None = None
    telegram_chat_id: str | None = None
    client_key: str = "unknown"  # rate-limit key: IP or "tg:<chat>"
    conversation_id: str | None = None  # v1.2: None starts a new conversation


@dataclass(frozen=True)
class RunLimits:
    default_max_iterations: int = 5
    max_iterations_limit: int = 8
    question_min_chars: int = 10
    question_max_chars: int = 2000
    revision_iterations: int = 2
    retention_days: int = 30
    memory_enabled: bool = True
    conversation_max_turns: int = 30  # v1.2
    followup_min_chars: int = 2  # v1.2: "and cost?" is a valid follow-up


class RunService:
    def __init__(
        self,
        runs: RunRepository,
        runner: JobRunner,
        supervisor: Supervisor,
        limits: RunLimits,
        ensure_llm_available: Callable[[], None] = lambda: None,
        memories: MemoryRepository | None = None,
        *,
        url_check: Callable[[str], None] = lambda url: None,  # raises FetchRefused (v1.1)
        indexer: Indexer | None = None,  # v1.1: removes the evidence of deleted runs
    ) -> None:
        self.runs, self.runner, self.supervisor, self.limits = runs, runner, supervisor, limits
        self._ensure_llm = ensure_llm_available  # raises LLMQuotaExceeded (service level)
        self._memories = memories
        self._url_check, self._indexer = url_check, indexer
        self._input = InputValidator()

    # ---- submit ----------------------------------------------------------------------------

    def check_input(
        self, question: str, max_iterations: int | None, min_chars: int | None = None
    ) -> tuple[str, int, list[str]]:
        """Free checks: (sanitized question, iterations, injection pattern names)."""
        minimum = self.limits.question_min_chars if min_chars is None else min_chars
        clean = self._input.sanitize(question)
        if len(clean) > self.limits.question_max_chars:
            raise InputTooLarge(
                f"The question has {len(clean)} characters; the limit is "
                f"{self.limits.question_max_chars}."
            )
        if len(clean) < minimum:
            raise InvalidField(
                f"Write the question in at least {minimum} characters.",
                "#/question",
            )
        iterations = max_iterations or self.limits.default_max_iterations
        if not 1 <= iterations <= self.limits.max_iterations_limit:
            raise InvalidField(
                f"Must be between 1 and {self.limits.max_iterations_limit}.", "#/max_iterations"
            )
        return clean, iterations, self._input.check(clean)

    def check_command(self, cmd: SubmitCommand) -> tuple[str, int, list[str]]:
        """Free checks for a whole command: input, sources, callback URL."""
        iterations = cmd.max_iterations or PROFILES[cmd.depth].default_max_iterations
        followup = cmd.conversation_id is not None and bool(self.conversation_turns(cmd))
        minimum = self.limits.followup_min_chars if followup else None
        clean, iterations, patterns = self.check_input(cmd.question, iterations, minimum)
        if not cmd.sources or not cmd.sources <= REQUESTABLE_SOURCES:
            raise InvalidField("Choose at least one of: web, wikipedia, documents.", "#/sources")
        if cmd.callback_url:
            try:
                self._url_check(cmd.callback_url)
            except FetchRefused as err:
                raise InvalidCallbackUrl(err.reason) from err
        return clean, iterations, patterns

    def conversation_turns(self, cmd: SubmitCommand) -> list[ResearchRun]:
        """v1.2 rules for continuing a conversation (plan Improving_Chat_UI_UX §4.3):
        unknown → 404, full → 409, a turn still in progress → 409. [] for a new one."""
        if cmd.conversation_id is None:
            return []
        turns = self.runs.conversation(cmd.conversation_id, self.limits.conversation_max_turns + 1)
        if not turns:
            raise ConversationNotFound(cmd.conversation_id)
        if len(turns) >= self.limits.conversation_max_turns:
            raise ConversationFull(self.limits.conversation_max_turns)
        if any(not t.done for t in turns):
            raise ConversationBusy()
        return turns

    def conversation(self, conversation_id: str) -> list[ResearchRun]:
        turns = self.runs.conversation(conversation_id, self.limits.conversation_max_turns)
        if not turns:
            raise ConversationNotFound(conversation_id)
        return turns

    def submit(self, cmd: SubmitCommand) -> ResearchRun:
        clean, iterations, patterns = self.check_command(cmd)
        earlier = self.conversation_turns(cmd)
        self._ensure_llm()
        if not self.runner.has_capacity():
            raise QueueFull()
        subscribers = []
        if cmd.callback_url:
            subscribers.append(Subscriber("callback", cmd.callback_url))
        if cmd.telegram_chat_id:
            subscribers.append(Subscriber("telegram", cmd.telegram_chat_id))
        run = ResearchRun.create(
            clean,
            iterations,
            cmd.require_approval and cmd.channel is Channel.WEB,
            cmd.use_memory and self.limits.memory_enabled,
            depth=cmd.depth,
            sources=cmd.sources,
            workspace_id=cmd.workspace_id,
            channel=cmd.channel,
            subscribers=subscribers,
            conversation_id=cmd.conversation_id,
            turn=len(earlier) + 1,
        )
        if cmd.telegram_chat_id:  # "working on it", committed with the run (one transaction)
            run.queue_outbox(
                OutboxMessage.new(
                    run.id,
                    "telegram",
                    EVENT_ACCEPTED,
                    cmd.telegram_chat_id,
                    {"chat_id": cmd.telegram_chat_id},
                )
            )
        if patterns:
            run.notice(
                "The question contains phrases often used in prompt-injection attempts "
                f"({', '.join(patterns)}); they are treated as data, not instructions.",
                "possible_injection",
            )
            logger.warning("run=%s possible_injection patterns=%s", run.id, ",".join(patterns))
        self.runs.create(run)
        self._queue(run)
        # Never log the question text: ids, sizes and flags only.
        logger.info(
            "run=%s submitted channel=%s depth=%s chars=%d iterations=%d approval=%s memory=%s",
            run.id,
            run.channel.value,
            run.depth.value,
            len(clean),
            iterations,
            run.require_approval,
            run.use_memory,
        )
        return self.runs.get(run.id)

    def _queue(self, run: ResearchRun) -> None:
        try:
            self.runner.submit(run.id)
        except QueueFull:
            # Rare race (the queue filled since the capacity check): never leave it queued.
            run = self.runs.get(run.id)
            run.fail("The server was too busy to start this run; please retry.")
            self.runs.save(run)
            raise

    # ---- read ------------------------------------------------------------------------------

    def get(self, run_id: str) -> ResearchRun:
        return self.runs.get(run_id)

    def wait(self, run_id: str, timeout_s: float, poll_s: float = 0.2) -> ResearchRun:
        deadline = time.monotonic() + timeout_s
        run = self.runs.get(run_id)
        while not run.done and time.monotonic() < deadline:
            time.sleep(poll_s)
            run = self.runs.get(run_id)
        return run

    # ---- human approval (extension) ------------------------------------------------------

    def approve(self, run_id: str) -> ResearchRun:
        run = self.runs.get(run_id)
        run.approve()
        self.runs.save(run)
        self.supervisor.remember(run)
        return run

    def reject(self, run_id: str, feedback: str | None) -> ResearchRun:
        run = self.runs.get(run_id)
        revising = bool(feedback) and run.revisions == 0
        if run.status is RunStatus.AWAITING_APPROVAL and revising:
            if not self.runner.has_capacity():
                raise QueueFull()
        if run.reject(feedback, self.limits.revision_iterations):
            self.runs.save(run)
            self._queue(run)
        else:
            self.runs.save(run)
        return self.runs.get(run_id)

    # ---- housekeeping ----------------------------------------------------------------------

    def recover(self) -> int:
        """Runs left queued or running by a restart cannot resume: mark them failed."""
        stuck = self.runs.runs_in({RunStatus.QUEUED, RunStatus.RUNNING})
        for run in stuck:
            run.fail("Interrupted by a server restart; please submit the question again.")
            self.runs.save(run)
        return len(stuck)

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        expired = 0
        for run in self.runs.runs_in({RunStatus.AWAITING_APPROVAL}):
            if run.approval_expired(now):
                run.cancel("The approval request expired.")
                self.runs.save(run)
                expired += 1
        cutoff = (now - timedelta(days=self.limits.retention_days)).isoformat(timespec="seconds")
        deleted_ids = self.runs.delete_older_than(cutoff)
        if self._indexer is not None:  # v1.1: their evidence goes too
            for run_id in deleted_ids:
                self._indexer.remove(f"run:{run_id}")
        deleted = len(deleted_ids)
        forgotten = self._memories.delete_older_than(cutoff) if self._memories else 0
        if expired or deleted or forgotten:
            logger.info("sweep expired=%d deleted=%d forgotten=%d", expired, deleted, forgotten)
        return {"expired": expired, "deleted": deleted, "forgotten": forgotten}
