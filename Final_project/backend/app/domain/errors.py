"""Named failures. Mapped to RFC 9457 problems only in app.api."""

from typing import Literal


class DomainError(Exception):
    """Base class for every expected failure."""


# ---- input -------------------------------------------------------------------------


class InvalidField(DomainError):
    """Invalid input that maps to one request field (JSON Pointer)."""

    def __init__(self, message: str, pointer: str) -> None:
        super().__init__(message)
        self.pointer = pointer


class InputTooLarge(DomainError):
    pass


class RunNotFound(DomainError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"No run with id '{run_id}'.")
        self.run_id = run_id


class SampleNotFound(DomainError):
    def __init__(self, sample_id: str) -> None:
        super().__init__(f"No sample with id '{sample_id}'.")


class InvalidTransition(DomainError):
    def __init__(self, current: str, action: str) -> None:
        super().__init__(f"Cannot {action} a run that is '{current}'.")
        self.current = current


class ConversationNotFound(DomainError):
    """v1.2: unknown or swept conversation id."""

    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"No conversation with id '{conversation_id}'.")
        self.conversation_id = conversation_id


class ConversationFull(DomainError):
    def __init__(self, max_turns: int) -> None:
        super().__init__(f"This chat has reached {max_turns} questions. Start a new chat.")


class ConversationBusy(DomainError):
    def __init__(self) -> None:
        super().__init__("This chat is still answering. Wait for the current answer.")


class QueueFull(DomainError):
    def __init__(self) -> None:
        super().__init__("The server is busy: too many runs are waiting. Try again shortly.")


# ---- the supervisor loop -------------------------------------------------------------


class ProtocolError(DomainError):
    """The Supervisor's reply broke the DELEGATE/TASK/FINAL protocol. The message is
    written for the model: it goes into the repair prompt."""


class BudgetExceeded(DomainError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"The run reached its limit of {limit} real LLM calls.")
        self.limit = limit


class CanaryLeak(DomainError):
    def __init__(self) -> None:
        super().__init__("A response leaked internal instructions; the run was stopped.")


class KnowledgeUnavailable(DomainError):
    """The knowledge source (Wikipedia) could not be reached."""


# ---- LLM provider (from Labs 2-4) ------------------------------------------------------


class LLMError(DomainError):
    """Base class for failures talking to the LLM provider."""


class LLMQuotaExceeded(LLMError):
    def __init__(self, scope: Literal["minute", "day"], retry_after_s: float) -> None:
        super().__init__(f"LLM quota exceeded ({scope}); retry after {retry_after_s:.0f}s.")
        self.scope = scope
        self.retry_after_s = retry_after_s


class LLMUnavailable(LLMError):
    def __init__(self, reason: str, retry_after_s: float = 10) -> None:
        super().__init__(reason)
        self.retry_after_s = retry_after_s


class LLMOverloaded(LLMUnavailable):
    """The provider is temporarily overloaded (HTTP 5xx) — worth a short retry."""


class LLMTimeout(LLMError):
    pass


class LLMRequestRejected(LLMError):
    """The provider refused the request (bad key, invalid request, blocked content)."""

    def __init__(self, status: int | None, reason: str) -> None:
        super().__init__(reason)
        self.status = status


class LLMBadResponse(LLMError):
    """The model's answer could not be turned into a valid result."""


# ---- tools: search and fetching (v1.1) ---------------------------------------------------


class SearchUnavailable(DomainError):
    """Web search failed (timeout, bad response, every engine blocked)."""


class FetchRefused(DomainError):
    """The SSRF guard refused a URL. `reason` names the rule, never a resolved address."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchFailed(DomainError):
    """A permitted fetch failed (timeout, http 404, too large, unsupported type…)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---- RAG and documents (v1.1) -------------------------------------------------------------


class ModelsLoading(DomainError):
    def __init__(self) -> None:
        super().__init__("The search models are still loading; try again in a moment.")


class ExtractionError(DomainError):
    """Text could not be extracted from a document. The message is user-safe."""


class InvalidWorkspace(DomainError):
    def __init__(self) -> None:
        super().__init__("Send a valid X-Workspace-Id header (a lowercase UUIDv4).")


class DocumentNotFound(DomainError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"No document with id '{document_id}' in this workspace.")
        self.document_id = document_id


class DemoReadOnly(DomainError):
    def __init__(self) -> None:
        super().__init__("Demo library documents cannot be deleted.")


class UnsupportedMediaType(DomainError):
    def __init__(self) -> None:
        super().__init__("Only .md, .txt and .pdf files are accepted (checked by content).")


class FileTooLarge(DomainError):
    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"The file is larger than {limit_bytes // (1024 * 1024)} MB.")
        self.limit_bytes = limit_bytes


class WorkspaceQuotaExceeded(DomainError):
    pass


class IndexingBusy(DomainError):
    def __init__(self) -> None:
        super().__init__("Too many documents are being indexed; try again shortly.")


# ---- webhooks (v1.1) ------------------------------------------------------------------------


class InvalidCallbackUrl(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"callback_url is not allowed: {reason}.")
        self.reason = reason


class WebhookUnauthorized(DomainError):
    def __init__(self) -> None:
        super().__init__("Missing or wrong webhook secret.")


# ---- step 3 stubs ---------------------------------------------------------------------------


class FeatureNotReady(DomainError):
    """Raised by contract stubs until their owner implements them (501). Remove its uses."""

    def __init__(self, feature: str) -> None:
        super().__init__(f"{feature} is not implemented yet.")
        self.feature = feature
