"""/documents routes (step 3 STUB): the contract's paths and headers, answering 501 until RAG
implements DocumentService (RAG_PLAN §3.6). Owner: RAG."""

from fastapi import APIRouter, Header

from app.domain.documents import valid_workspace_id
from app.domain.errors import FeatureNotReady, InvalidWorkspace

from .problems import PROBLEM_RESPONSE

router = APIRouter(prefix="/documents", tags=["documents"])
_ERRORS = {code: PROBLEM_RESPONSE for code in (400, 404, 413, 415, 422, 429, 501, 507)}


def _workspace(x_workspace_id: str | None) -> str:
    if not valid_workspace_id(x_workspace_id):
        raise InvalidWorkspace()
    return x_workspace_id or ""


@router.post("", status_code=202, responses=_ERRORS)
def upload(x_workspace_id: str | None = Header(None)) -> dict:
    _workspace(x_workspace_id)
    raise FeatureNotReady("Document upload")


@router.get("", responses=_ERRORS)
def list_documents(x_workspace_id: str | None = Header(None)) -> dict:
    _workspace(x_workspace_id)
    raise FeatureNotReady("The document library")


@router.get("/{document_id}", responses=_ERRORS)
def get_document(document_id: str, x_workspace_id: str | None = Header(None)) -> dict:
    _workspace(x_workspace_id)
    raise FeatureNotReady("The document library")


@router.delete("/{document_id}", status_code=204, responses=_ERRORS)
def delete_document(document_id: str, x_workspace_id: str | None = Header(None)) -> None:
    _workspace(x_workspace_id)
    raise FeatureNotReady("The document library")
