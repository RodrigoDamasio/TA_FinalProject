import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiError, fieldLabel, parseRetryAfter, toApiError } from "@/lib/api";
import { completedRun, delivery, doc, raw } from "./fixtures";

const WS = "2a97516c-1111-4222-8333-444455556666";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  // a new Response per call: a body can be read once
  const fetchMock = vi.fn().mockImplementation(
    async () =>
      new Response(status === 204 ? null : typeof body === "string" ? body : JSON.stringify(body), { status, headers }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const problem = (slug: string, status: number, extra: object = {}) => ({
  type: `http://localhost:8000/problems/${slug}`,
  title: "T",
  status,
  detail: `detail of ${slug}`,
  instance: "/research",
  ...extra,
});

async function errorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError) return err;
  }
  throw new Error("expected an ApiError");
}

afterEach(() => vi.unstubAllGlobals());

describe("requests", () => {
  it("starts a run with question, depth, sources, settings and the workspace header", async () => {
    const fetchMock = respond(202, raw.accepted);
    const input = { question: "What is RAG and why?", depth: "brief" as const, sources: ["documents" as const], require_approval: false, use_memory: true };
    const accepted = await api.startResearch(input, WS);
    expect(accepted.run_id).toBe(raw.accepted.run_id);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/research");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "X-Workspace-Id": WS, "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual(input);
    expect(JSON.parse(init.body)).not.toHaveProperty("callback_url");
  });

  it("reads runs, approves, rejects and lists deliveries", async () => {
    let fetchMock = respond(200, raw.completed);
    expect((await api.getResearch(completedRun.run_id)).report?.title).toBe(completedRun.report?.title);
    expect(fetchMock.mock.calls[0][0]).toBe(`http://localhost:8000/research/${completedRun.run_id}`);
    fetchMock = respond(200, raw.completed);
    await api.approve("run_1");
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/research\/run_1\/approve$/);
    fetchMock = respond(200, raw.completed);
    await api.reject("run_1", "more detail");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ feedback: "more detail" });
    respond(200, [delivery]);
    expect(await api.deliveries("run_1")).toEqual([delivery]);
    expect(api.eventsUrl("run_1")).toBe("http://localhost:8000/research/run_1/events");
  });

  it("reads health, agents and samples; uses NEXT_PUBLIC_API_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example/");
    const fetchMock = respond(200, raw.health);
    expect((await api.health()).search?.available).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.example/health");
    respond(200, raw.agents);
    expect((await api.agents()).map((a) => a.name)).toContain("fact_checker");
    respond(200, [{ id: "s1", title: "T", question: "Q?" }]);
    expect(await api.samples()).toEqual([{ id: "s1", title: "T", question: "Q?" }]);
    respond(200, { ...raw.completed, sample: true });
    expect((await api.sample("s1")).sample).toBe(true);
  });

  it("v1.2: a follow-up sends conversation_id; GET /conversations/{id}; GET /tools", async () => {
    let fetchMock = respond(202, raw.acceptedFollowup);
    const input = {
      question: "and cost?",
      depth: "brief" as const,
      sources: ["web" as const],
      require_approval: false,
      use_memory: true,
      conversation_id: raw.acceptedFollowup.conversation_id,
    };
    expect(await api.startResearch(input, WS)).toMatchObject({ turn: 2, conversation_id: input.conversation_id });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).conversation_id).toBe(input.conversation_id);
    fetchMock = respond(200, raw.conversation);
    const conv = await api.conversation(raw.conversation.conversation_id);
    expect(conv.turns).toHaveLength(2);
    expect(fetchMock.mock.calls[0][0]).toBe(`http://localhost:8000/conversations/${raw.conversation.conversation_id}`);
    respond(200, [{ name: "search_conversation", family: "conversation", description: "d", parameters: {} }]);
    expect((await api.tools())[0].family).toBe("conversation");
  });

  it("v1.2 problems recorded from the server keep their messages", async () => {
    const p = raw.problems;
    respond(409, p["conversation-full"]);
    expect((await errorOf(api.getResearch("run_1"))).message).toBe("This chat has reached 1 questions. Start a new chat.");
    respond(409, p["conversation-busy"]);
    expect((await errorOf(api.getResearch("run_1"))).message).toMatch(/Wait for the current answer/);
    respond(404, p["conversation-not-found"]);
    expect((await errorOf(api.getResearch("run_1"))).message).toMatch(/Start a new chat/);
    respond(409, { ...p["conversation-full"], detail: null });
    expect((await errorOf(api.getResearch("run_1"))).message).toBe("This chat is full. Start a new chat.");
    expect(fieldLabel("#/conversation_id")).toBe("Chat");
  });

  it("documents: list, get and delete with the workspace header", async () => {
    let fetchMock = respond(200, raw.documents);
    const list = await api.listDocuments(WS);
    expect(list.demo.length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ "X-Workspace-Id": WS });
    fetchMock = respond(200, doc());
    expect((await api.getDocument("doc_aaaa", WS)).name).toBe("notes.md");
    fetchMock = respond(204, null);
    await api.deleteDocument("doc_aaaa", WS);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE", headers: { "X-Workspace-Id": WS } });
  });
});

describe("errors (RFC 9457)", () => {
  const cases: [string, number, api.ApiErrorKind][] = [
    ["validation-error", 422, "validation"],
    ["invalid-workspace", 400, "validation"],
    ["invalid-callback-url", 422, "validation"],
    ["input-too-large", 413, "too_large"],
    ["file-too-large", 413, "too_large"],
    ["unsupported-media-type", 415, "unsupported_type"],
    ["workspace-quota-exceeded", 507, "quota_exceeded"],
    ["run-not-found", 404, "not_found"],
    ["document-not-found", 404, "not_found"],
    ["sample-not-found", 404, "not_found"],
    ["invalid-transition", 409, "conflict"],
    ["demo-read-only", 409, "conflict"],
    ["rate-limited", 429, "rate_limited"],
    ["llm-quota-exhausted", 503, "quota"],
    ["llm-unavailable", 503, "busy"],
    ["wait-timeout", 504, "timeout"],
    ["conversation-not-found", 404, "conversation_not_found"],
    ["conversation-full", 409, "conversation_full"],
    ["conversation-busy", 409, "conversation_busy"],
  ];
  it.each(cases)("%s → %s", async (slug, status, kind) => {
    respond(status, problem(slug, status), { "Content-Type": "application/problem+json", "Retry-After": "29" });
    const err = await errorOf(api.getResearch("run_1"));
    expect(err.kind).toBe(kind);
    expect(err.slug).toBe(slug);
    expect(err.status).toBe(status);
    expect(err.retryAfter).toBe(29);
  });

  it("maps field errors to labels (real 422 body)", async () => {
    respond(
      422,
      problem("validation-error", 422, { errors: [{ detail: "Write the question in at least 10 characters.", pointer: "#/question" }] }),
    );
    const err = await errorOf(api.getResearch("run_1"));
    expect(err.fieldErrors).toEqual(["Question: Write the question in at least 10 characters."]);
    expect(fieldLabel("#/")).toBe("Request");
    expect(fieldLabel("#/max_iterations")).toBe("Max iterations");
    expect(fieldLabel("#/some_field/0")).toBe("Some field");
  });

  it("falls back on status codes and non-JSON bodies", async () => {
    const byStatus: [number, api.ApiErrorKind][] = [
      [400, "validation"],
      [404, "not_found"],
      [413, "too_large"],
      [415, "unsupported_type"],
      [429, "rate_limited"],
      [503, "busy"],
      [507, "quota_exceeded"],
      [500, "server"],
    ];
    for (const [status, kind] of byStatus) {
      respond(status, "<html>proxy</html>");
      expect((await errorOf(api.health())).kind).toBe(kind);
    }
    expect(toApiError({ status: 500, body: { title: "Boom" } }).message).toBe("Boom");
  });

  it("Retry-After as seconds or an HTTP date", () => {
    const now = Date.parse("2026-10-11T12:00:00Z");
    expect(parseRetryAfter("30", now)).toBe(30);
    expect(parseRetryAfter("0", now)).toBeUndefined();
    expect(parseRetryAfter("Sun, 11 Oct 2026 12:01:00 GMT", now)).toBe(60);
    expect(parseRetryAfter("Sun, 11 Oct 2026 11:00:00 GMT", now)).toBeUndefined();
    expect(parseRetryAfter("soon", now)).toBeUndefined();
    expect(parseRetryAfter(null, now)).toBeUndefined();
  });

  it("a response that breaks the schema is a contract error", async () => {
    respond(200, { run_id: 1 });
    expect((await errorOf(api.getResearch("run_1"))).kind).toBe("contract");
    respond(200, "not json");
    expect((await errorOf(api.health())).kind).toBe("contract");
  });

  it("network failure and timeout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    expect((await errorOf(api.health())).kind).toBe("network");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("t", "TimeoutError")));
    expect((await errorOf(api.health())).kind).toBe("timeout");
  });
});

describe("upload (XMLHttpRequest with progress)", () => {
  class FakeXHR {
    static last: FakeXHR;
    static reply: { status: number; body: string; retryAfter?: string } | "error" | "timeout" = { status: 202, body: "{}" };
    method = "";
    url = "";
    headers: Record<string, string> = {};
    timeout = 0;
    status = 0;
    responseText = "";
    sent: FormData | null = null;
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    constructor() {
      FakeXHR.last = this;
    }
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(k: string, v: string) {
      this.headers[k] = v;
    }
    getResponseHeader(name: string) {
      const r = FakeXHR.reply;
      return name === "Retry-After" && typeof r === "object" ? (r.retryAfter ?? null) : null;
    }
    send(body: FormData) {
      this.sent = body;
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      this.upload.onprogress?.({ lengthComputable: false, loaded: 0, total: 0 });
      const r = FakeXHR.reply;
      queueMicrotask(() => {
        if (r === "error") return this.onerror?.();
        if (r === "timeout") return this.ontimeout?.();
        this.status = r.status;
        this.responseText = r.body;
        this.onload?.();
      });
    }
  }
  const file = new File(["# hi"], "notes.md", { type: "text/markdown" });

  it("sends multipart with the workspace header and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    FakeXHR.reply = { status: 202, body: JSON.stringify(doc({ status: "indexing" })) };
    const progress: number[] = [];
    const d = await api.uploadDocument(file, WS, (f) => progress.push(f));
    expect(d.status).toBe("indexing");
    expect(progress).toEqual([0.5]);
    const x = FakeXHR.last;
    expect([x.method, x.url, x.headers["X-Workspace-Id"]]).toEqual(["POST", "http://localhost:8000/documents", WS]);
    expect((x.sent?.get("file") as File).name).toBe("notes.md");
  });

  it("maps problems, contract, network and timeout errors", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    FakeXHR.reply = { status: 415, body: JSON.stringify(problem("unsupported-media-type", 415)) };
    expect((await errorOf(api.uploadDocument(file, WS))).kind).toBe("unsupported_type");
    FakeXHR.reply = { status: 429, body: "nope", retryAfter: "12" };
    const limited = await errorOf(api.uploadDocument(file, WS));
    expect([limited.kind, limited.retryAfter]).toEqual(["rate_limited", 12]);
    FakeXHR.reply = { status: 202, body: JSON.stringify({ id: 1 }) };
    expect((await errorOf(api.uploadDocument(file, WS))).kind).toBe("contract");
    FakeXHR.reply = "error";
    expect((await errorOf(api.uploadDocument(file, WS))).kind).toBe("network");
    FakeXHR.reply = "timeout";
    expect((await errorOf(api.uploadDocument(file, WS))).kind).toBe("timeout");
  });
});
