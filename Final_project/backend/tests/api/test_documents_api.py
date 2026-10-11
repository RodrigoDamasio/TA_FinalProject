"""/documents end to end (fake embedder, inline indexing): upload, list, get, delete,
problems, the upload rate limit and workspace isolation."""

WS = "3f2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c"
OTHER = "9b2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c"


def h(ws=WS):
    return {"X-Workspace-Id": ws}


def test_upload_list_get_delete_and_isolation(api):
    a = api(raise_errors=False)
    body = b"# Notes\n\nHNSW tuning notes. ef and M control recall."
    up = a.client.post("/documents", files={"file": ("notes.md", body)}, headers=h())
    assert up.status_code == 202
    doc = up.json()
    assert doc["name"] == "notes.md" and doc["media_type"] == "text/markdown"
    got = a.client.get(f"/documents/{doc['id']}", headers=h()).json()
    assert got["status"] == "ready" and got["chunks"] == 1 and got["demo"] is False
    listing = a.client.get("/documents", headers=h()).json()
    assert (
        listing["workspace"]["documents"] == 1
        and listing["workspace"]["limits"]["max_documents"] == 10
    )
    assert [d["id"] for d in listing["documents"]] == [doc["id"]] and listing["demo"] == []
    assert a.client.get("/documents", headers=h(OTHER)).json() == {
        "workspace": None, "documents": [], "demo": [],
    }  # fmt: skip
    assert a.client.get(f"/documents/{doc['id']}", headers=h(OTHER)).status_code == 404
    assert a.client.delete(f"/documents/{doc['id']}", headers=h(OTHER)).status_code == 404
    assert a.client.delete(f"/documents/{doc['id']}", headers=h()).status_code == 204
    assert (
        a.client.get(f"/documents/{doc['id']}", headers=h())
        .json()["type"]
        .endswith("/document-not-found")
    )


def test_document_problems_and_rate_limit(api):
    a = api(raise_errors=False, rate_limit_uploads_per_hour=2, upload_max_bytes=100)
    assert a.client.get("/documents").json()["type"].endswith("/invalid-workspace")
    bad = a.client.post("/documents", files={"file": ("x.exe", b"MZ")}, headers=h())
    assert bad.status_code == 415 and bad.json()["type"].endswith("/unsupported-media-type")
    big = a.client.post("/documents", files={"file": ("x.txt", b"a" * 200)}, headers=h())
    assert big.status_code == 413 and big.json()["type"].endswith("/file-too-large")
    limited = a.client.post("/documents", files={"file": ("y.txt", b"text")}, headers=h())
    assert limited.status_code == 429


def test_a_research_run_finds_and_copies_uploaded_evidence(api):
    a = api()
    a.client.post("/documents", files={"file": ("handbook.md", b"# Handbook\n\nThe Zebra-Harbor "
                                                 b"protocol rotates keys every 90 days.")},
                  headers=h())  # fmt: skip
    r = a.client.post(
        "/research?wait=true",
        json={"question": "How often does the Zebra-Harbor protocol rotate keys?",
              "sources": ["documents"]},
        headers=h(),
    )  # fmt: skip
    view = r.json()
    tool = next(e for e in view["conversation_history"] if e["action"] == "TOOL_CALL")
    assert tool["tool"] == "search_documents" and tool["status"] == "ok" and tool["evidence"]
    assert view["report"]["sources"][0]["document_name"] == "handbook.md"
