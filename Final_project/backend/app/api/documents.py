"""/documents (RAG_PLAN §3.6, BACKEND_PLAN §14.1, §14.5, v1.1 G6). Every request names its
workspace with X-Workspace-Id (a browser-made UUIDv4, used as a capability). Owner: RAG."""

from typing import Any

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile

from app.domain.documents import Document, Workspace, valid_workspace_id
from app.domain.errors import FileTooLarge, InvalidWorkspace

from .dependencies import Container, get_container
from .problems import PROBLEM_RESPONSE

router = APIRouter(prefix="/documents", tags=["documents"])
_ERRORS = {code: PROBLEM_RESPONSE for code in (400, 404, 409, 413, 415, 422, 429, 503, 507)}


def _workspace(x_workspace_id: str | None) -> str:
    if not valid_workspace_id(x_workspace_id):
        raise InvalidWorkspace()
    return x_workspace_id or ""


def document_view(d: Document) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "media_type": d.media_type,
        "size_bytes": d.size_bytes,
        "status": d.status.value,
        "pages": d.pages,
        "chunks": d.chunks,
        "error": d.error,
        "created_at": d.created_at,
        "expires_at": d.expires_at,
        "demo": d.demo,
    }


def workspace_view(w: Workspace | None, c: Container) -> dict[str, Any] | None:
    if w is None:
        return None
    limits = c.documents.limits
    return {
        "id": w.id,
        "created_at": w.created_at,
        "expires_at": w.expires_at,
        "bytes_used": w.bytes_used,
        "documents": w.documents,
        "limits": {
            "max_documents": limits.workspace_max_docs,
            "max_bytes": limits.workspace_max_bytes,
        },
    }


@router.post("", status_code=202, responses=_ERRORS)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    x_workspace_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    """Upload a .md, .txt or .pdf file (checked by content) for indexing. 202 + `indexing`;
    poll GET /documents/{id} until `ready` or `failed`. A file already in the workspace
    returns the existing document."""
    workspace_id = _workspace(x_workspace_id)
    limit = c.documents.limits.upload_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 64 * 1024:
        raise FileTooLarge(limit)  # multipart overhead allowed
    c.upload_limiter.check(request.client.host if request.client else "unknown")
    data = await file.read(limit + 1)
    doc = c.documents.upload(workspace_id, file.filename or "document", data)
    return document_view(doc)


@router.get("", responses=_ERRORS)
def list_documents(
    x_workspace_id: str | None = Header(None), c: Container = Depends(get_container)
) -> dict[str, Any]:
    workspace, documents, demo = c.documents.list(_workspace(x_workspace_id))
    return {
        "workspace": workspace_view(workspace, c),
        "documents": [document_view(d) for d in documents],
        "demo": [document_view(d) for d in demo],
    }


@router.get("/{document_id}", responses=_ERRORS)
def get_document(
    document_id: str,
    x_workspace_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    return document_view(c.documents.get(_workspace(x_workspace_id), document_id))


@router.delete("/{document_id}", status_code=204, responses=_ERRORS)
def delete_document(
    document_id: str,
    x_workspace_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> Response:
    c.documents.delete(_workspace(x_workspace_id), document_id)
    return Response(status_code=204)
