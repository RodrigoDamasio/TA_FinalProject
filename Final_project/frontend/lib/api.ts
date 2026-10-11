/** The API client (Lab 4 pattern): fetch + Zod + ApiError mapped from RFC 9457 slugs. */
import { z } from "zod";
import {
  type Accepted,
  AcceptedSchema,
  type Agent,
  AgentSchema,
  type Delivery,
  DeliverySchema,
  type Depth,
  type DocumentList,
  DocumentListSchema,
  DocumentSchema,
  type DocumentView,
  type Health,
  HealthSchema,
  ProblemSchema,
  type ResearchView,
  ResearchViewSchema,
  type Sample,
  SampleSchema,
  type SourceChoice,
} from "./schemas";

export type ApiErrorKind =
  | "validation"
  | "too_large"
  | "not_found"
  | "conflict"
  | "rate_limited"
  | "quota"
  | "busy"
  | "timeout"
  | "unsupported_type"
  | "quota_exceeded"
  | "server"
  | "network"
  | "contract";

export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    message: string,
    public retryAfter?: number,
    public fieldErrors: string[] = [],
    public slug = "",
    public status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const TIMEOUT_MS = 30_000; // POST /research answers 202 at once; the work streams over SSE

export function apiUrl(): string {
  // Referenced directly so Next.js inlines the value at build time.
  return (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

const FIELD_LABELS: Record<string, string> = {
  question: "Question",
  depth: "Depth",
  sources: "Sources",
  max_iterations: "Max iterations",
  require_approval: "Require approval",
  use_memory: "Use memory",
  feedback: "Feedback",
  file: "File",
};

/** "#/max_iterations" → "Max iterations". */
export function fieldLabel(pointer: string): string {
  const head = pointer.replace(/^#\/?/, "").split("/").filter(Boolean)[0];
  if (!head) return "Request";
  return FIELD_LABELS[head] ?? head.charAt(0).toUpperCase() + head.slice(1).replaceAll("_", " ");
}

/** Retry-After as seconds: delta-seconds or an HTTP date (RFC 9110 §10.2.3). */
export function parseRetryAfter(value: string | null, now = Date.now()): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) {
    const n = Number(trimmed);
    return n > 0 ? n : undefined;
  }
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return undefined;
  const s = Math.ceil((at - now) / 1000);
  return s > 0 ? s : undefined;
}

interface ProblemInfo {
  status: number;
  retryAfter?: number;
  body: unknown;
}

/** Maps a problem+json body (or none) and its status to an ApiError. */
export function toApiError({ status, retryAfter, body }: ProblemInfo): ApiError {
  let detail: string | undefined;
  let title: string | undefined;
  let slug = "";
  let fieldErrors: string[] = [];
  const problem = ProblemSchema.safeParse(body);
  if (problem.success) {
    detail = problem.data.detail ?? undefined;
    title = problem.data.title;
    slug = problem.data.type?.split("/").pop() ?? "";
    fieldErrors = (problem.data.errors ?? []).map((e) => `${fieldLabel(e.pointer)}: ${e.detail}`);
  }
  const err = (kind: ApiErrorKind, message: string, fields: string[] = []) =>
    new ApiError(kind, message, retryAfter, fields, slug, status);

  switch (slug) {
    case "validation-error":
    case "malformed-request":
      return err("validation", detail ?? "The request is not valid.", fieldErrors);
    case "invalid-workspace":
      return err("validation", "This browser's workspace id is not valid. Reload the page.");
    case "invalid-callback-url":
      return err("validation", detail ?? "The callback URL is not allowed.");
    case "input-too-large":
      return err("too_large", detail ?? "The question is too long.");
    case "file-too-large":
      return err("too_large", detail ?? "The file is too large.");
    case "unsupported-media-type":
      return err("unsupported_type", detail ?? "Only .md, .txt and .pdf files are accepted.");
    case "workspace-quota-exceeded":
      return err("quota_exceeded", detail ?? "Your workspace is full. Delete a document first.");
    case "run-not-found":
      return err("not_found", "This run is no longer available. Runs are kept for 30 days.");
    case "document-not-found":
      return err("not_found", "This document is no longer available.");
    case "sample-not-found":
      return err("not_found", "Sample not found.");
    case "invalid-transition":
      return err("conflict", "The run already moved on.");
    case "demo-read-only":
      return err("conflict", "Demo documents are read-only.");
    case "rate-limited":
      return err("rate_limited", "You started too many runs in a short time.");
    case "llm-quota-exhausted":
      return err("quota", "Today's free AI quota is used up. The samples still work.");
    case "llm-unavailable":
      return err("busy", "The research service is busy right now.");
    case "wait-timeout":
      return err("timeout", "The run took too long to finish.");
  }
  if (status === 400 || status === 422) return err("validation", detail ?? "The request is not valid.", fieldErrors);
  if (status === 404) return err("not_found", "Not found.");
  if (status === 413) return err("too_large", detail ?? "Too large.");
  if (status === 415) return err("unsupported_type", detail ?? "Unsupported file type.");
  if (status === 429) return err("rate_limited", "Too many requests. Please wait a moment.");
  if (status === 503) return err("busy", "The research service is busy right now.");
  if (status === 507) return err("quota_exceeded", detail ?? "Your workspace is full.");
  return err("server", title ?? "Something went wrong on the server. Please try again.");
}

async function errorFrom(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => undefined); // a proxy error page is not JSON
  return toApiError({ status: res.status, retryAfter: parseRetryAfter(res.headers.get("Retry-After")), body });
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${apiUrl()}${path}`, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (err) {
    if (err instanceof DOMException && err.name === "TimeoutError") {
      throw new ApiError("timeout", "The server took too long to answer. Please try again.");
    }
    throw new ApiError("network", "Can't reach the research service. Check your connection.");
  }
  if (!res.ok) throw await errorFrom(res);
  return res;
}

/** A response that does not match its schema is a broken contract, never rendered. */
export function parseOrThrow<T>(schema: z.ZodType<T>, data: unknown): T {
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    throw new ApiError("contract", "Unexpected response from the server.");
  }
  return parsed.data;
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const res = await send(path, init);
  return parseOrThrow(schema, await res.json().catch(() => undefined));
}

const json = (method: string, body?: unknown, headers: Record<string, string> = {}): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json", ...headers },
  body: body === undefined ? undefined : JSON.stringify(body),
});
const ws = (workspaceId: string): Record<string, string> => ({ "X-Workspace-Id": workspaceId });

const enc = encodeURIComponent;

export interface ResearchInput {
  question: string;
  depth: Depth;
  sources: SourceChoice[];
  max_iterations?: number | null;
  require_approval: boolean;
  use_memory: boolean;
}

export const health = (): Promise<Health> => request("/health", HealthSchema);
export const agents = (): Promise<Agent[]> => request("/agents", z.array(AgentSchema));
export const samples = (): Promise<Sample[]> => request("/samples", z.array(SampleSchema));
export const sample = (id: string): Promise<ResearchView> =>
  request(`/samples/${enc(id)}`, ResearchViewSchema);

/** Starts a run (202). The caller follows it with getResearch() and the events stream.
 * The web UI never sends a callback_url (FRONTEND_PLAN §5). */
export const startResearch = (input: ResearchInput, workspaceId: string): Promise<Accepted> =>
  request("/research", AcceptedSchema, json("POST", input, ws(workspaceId)));
export const getResearch = (id: string): Promise<ResearchView> =>
  request(`/research/${enc(id)}`, ResearchViewSchema);
export const approve = (id: string): Promise<ResearchView> =>
  request(`/research/${enc(id)}/approve`, ResearchViewSchema, json("POST"));
export const reject = (id: string, feedback: string | null): Promise<ResearchView> =>
  request(`/research/${enc(id)}/reject`, ResearchViewSchema, json("POST", { feedback }));
export const deliveries = (id: string): Promise<Delivery[]> =>
  request(`/research/${enc(id)}/deliveries`, z.array(DeliverySchema));

export const eventsUrl = (id: string): string => `${apiUrl()}/research/${enc(id)}/events`;

// ---- documents ---------------------------------------------------------------------------

export const listDocuments = (workspaceId: string): Promise<DocumentList> =>
  request("/documents", DocumentListSchema, { headers: ws(workspaceId) });
export const getDocument = (id: string, workspaceId: string): Promise<DocumentView> =>
  request(`/documents/${enc(id)}`, DocumentSchema, { headers: ws(workspaceId) });
export async function deleteDocument(id: string, workspaceId: string): Promise<void> {
  await send(`/documents/${enc(id)}`, { method: "DELETE", headers: ws(workspaceId) });
}

/** Multipart upload with progress (fetch has no upload progress: XMLHttpRequest). */
export function uploadDocument(
  file: File,
  workspaceId: string,
  onProgress?: (fraction: number) => void,
): Promise<DocumentView> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiUrl()}/documents`);
    xhr.setRequestHeader("X-Workspace-Id", workspaceId);
    xhr.timeout = 120_000;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(parseOrThrow(DocumentSchema, body));
        } catch (err) {
          reject(err);
        }
        return;
      }
      reject(toApiError({ status: xhr.status, retryAfter: parseRetryAfter(xhr.getResponseHeader("Retry-After")), body }));
    };
    xhr.onerror = () => reject(new ApiError("network", "Can't reach the research service. Check your connection."));
    xhr.ontimeout = () => reject(new ApiError("timeout", "The upload took too long. Please try again."));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
