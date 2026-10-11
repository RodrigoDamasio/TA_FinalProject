/** Fixtures captured from a REAL fake-mode backend (LLM_MODE=fake, 0 real calls):
 * tests/fixtures/*.json are verbatim responses of /health, /agents, POST /research,
 * GET /research/{id}, the SSE stream and /documents. They are parsed with the frontend's
 * schemas here, so a backend shape change fails the tests. Variants (tool-call statuses,
 * deliveries, a fact check) are built on top of the real shapes. */
import { z } from "zod";
import accepted from "./fixtures/accepted.json";
import agentsJson from "./fixtures/agents.json";
import documentsJson from "./fixtures/documents.json";
import documentsUploadJson from "./fixtures/documents-upload.json";
import eventsJson from "./fixtures/events.json";
import healthJson from "./fixtures/health.json";
import approvalJson from "./fixtures/run-approval.json";
import completedJson from "./fixtures/run-completed.json";
import documentsRunJson from "./fixtures/run-documents.json";
import {
  AcceptedSchema,
  AgentSchema,
  type Delivery,
  DocumentListSchema,
  type DocumentView,
  type Entry,
  EventSchemas,
  type EventType,
  type Health,
  HealthSchema,
  type ResearchView,
  ResearchViewSchema,
  type RunEvent,
  type Sample,
  type ToolCallEntry,
} from "@/lib/schemas";

export const raw = {
  accepted,
  agents: agentsJson,
  documents: documentsJson,
  documentsUpload: documentsUploadJson,
  events: eventsJson,
  health: healthJson,
  approval: approvalJson,
  completed: completedJson,
  documentsRun: documentsRunJson,
};

export const completedRun: ResearchView = ResearchViewSchema.parse(completedJson);
export const documentsRun: ResearchView = ResearchViewSchema.parse(documentsRunJson);
export const approvalRun: ResearchView = ResearchViewSchema.parse(approvalJson);
export const health: Health = HealthSchema.parse(healthJson);
export const agentsList = z.array(AgentSchema).parse(agentsJson);
export const acceptedView = AcceptedSchema.parse(accepted);
export const documentList = DocumentListSchema.parse(documentsJson);
export const documentListUpload = DocumentListSchema.parse(documentsUploadJson);

/** The SSE stream of completedRun, as parsed events. */
export const liveEvents: RunEvent[] = (eventsJson as { id: number; type: string; data: unknown }[]).map(
  (e) => ({ id: e.id, type: e.type, data: EventSchemas[e.type as EventType].parse(e.data) }) as RunEvent,
);

export const RUN = completedRun.run_id;

export function runView(overrides: Partial<ResearchView> = {}): ResearchView {
  return { ...completedRun, ...overrides };
}

/** completedRun as it looked while running: the first n history entries, no report yet. */
export function partial(n: number, overrides: Partial<ResearchView> = {}): ResearchView {
  return runView({
    status: "running",
    report: null,
    final_output: null,
    conversation_history: completedRun.conversation_history.slice(0, n),
    metadata: { ...completedRun.metadata, finished_at: null, duration_seconds: null },
    ...overrides,
  });
}

const baseTool = completedRun.conversation_history.find((e) => e.action === "TOOL_CALL") as ToolCallEntry;

export function toolCall(overrides: Partial<ToolCallEntry> = {}): ToolCallEntry {
  return { ...baseTool, ...overrides };
}

export function notice(overrides: Partial<Extract<Entry, { action: "NOTICE" }>> = {}): Entry {
  return {
    seq: 50,
    iteration: 1,
    agent: "System",
    model: null,
    llm_calls: 0,
    cached: false,
    duration_ms: 0,
    tokens: { input: 0, output: 0, thinking: 0 },
    action: "NOTICE",
    text: "The tool-call limit was reached.",
    flag: "cap_reached",
    ...overrides,
  };
}

/** A Researcher task that searched, got weak results, rewrote, fell back and hit the cap. */
export const toolStory: ToolCallEntry[] = [
  toolCall({ seq: 2, status: "ok", weak: true, args: { query: "rag" }, results: 2, evidence: ["E1", "E2"] }),
  toolCall({
    seq: 3,
    tool: "search_wikipedia",
    status: "empty",
    provider: "wikipedia",
    args: { query: "retrieval augmented generation" },
    results: 0,
    evidence: [],
  }),
  toolCall({
    seq: 4,
    status: "ok",
    provider: "wikipedia",
    fallback: "wikipedia",
    args: { query: "retrieval augmented generation definition" },
    results: 3,
    evidence: ["E3", "E4", "E5"],
  }),
  toolCall({ seq: 5, tool: "read_result", status: "capped", provider: null, args: { evidence_id: "E3" }, results: 0, evidence: [] }),
];

export const delivery: Delivery = {
  id: "msg_1",
  kind: "callback",
  event: "research.completed",
  target_host: "example.com",
  status: "delivered",
  attempts: 1,
  last_status: 200,
  last_error: null,
  next_attempt_at: null,
  delivered_at: "2026-10-11T12:00:00+00:00",
};

export function doc(overrides: Partial<DocumentView> = {}): DocumentView {
  return {
    id: "doc_aaaa",
    name: "notes.md",
    media_type: "text/markdown",
    size_bytes: 2048,
    status: "ready",
    pages: null,
    chunks: 3,
    error: null,
    created_at: "2026-10-11T12:00:00+00:00",
    expires_at: "2026-10-12T12:00:00+00:00",
    demo: false,
    ...overrides,
  };
}

export const samplesList: Sample[] = [
  { id: "rag-basics", title: "RAG basics", question: completedRun.question },
  { id: "webhooks-docs", title: "Webhooks from the docs", question: documentsRun.question },
];
