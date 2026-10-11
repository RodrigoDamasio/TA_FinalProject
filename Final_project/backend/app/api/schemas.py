"""Request and response models of the research endpoints (BACKEND_PLAN §14.2). The view
itself is built by application/views.py (v1.1)."""

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domain.depth import Depth
from app.domain.evidence import SourceKind
from app.domain.run import CONVERSATION_ID

MAX_QUESTION_FIELD_CHARS = (
    20_000  # hard cap before sanitizing; the real limit is QUESTION_MAX_CHARS
)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_FIELD_CHARS,
        description="The research question, 10–2,000 characters.",
    )
    depth: Depth = Field(Depth.DETAILED, description="brief · detailed · technical")
    max_iterations: int | None = Field(
        None, description="Supervisor decisions before forced completion (1–8; default by depth)."
    )
    sources: list[SourceKind] | None = Field(
        None,
        description="Allowed sources: web, wikipedia, documents (default: all). 'documents' "
        "searches the X-Workspace-Id workspace (if sent) and the demo library.",
    )
    require_approval: bool = Field(False, description="Pause for human approval before FINAL.")
    use_memory: bool = Field(True, description="Let the Supervisor recall earlier reports.")
    callback_url: HttpUrl | None = Field(
        None, description="HTTPS URL that receives a signed POST when the run ends."
    )
    conversation_id: str | None = Field(
        None,
        pattern=CONVERSATION_ID,
        description="v1.2: continue this conversation (a follow-up may be 2+ characters). "
        "Omit to start a new conversation.",
    )


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str | None = Field(None, max_length=1000)


class Accepted(BaseModel):
    run_id: str
    status: str
    status_url: str
    events_url: str
    web_url: str
    callback_secret: str | None = Field(
        None, description="Only when callback_url was given; shown once (Standard Webhooks)."
    )
    conversation_id: str = Field(description="v1.2: the conversation this run belongs to.")
    turn: int = Field(description="v1.2: 1-based position of this run in its conversation.")
