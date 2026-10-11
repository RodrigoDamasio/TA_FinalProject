/** Zod schemas mirroring the backend v1.2 API (BACKEND_PLAN §14, app/application/views.py,
 * app/domain/transcript.py `history_entry`). Every API response and every SSE event is
 * parsed; invalid data is never rendered. Unknown keys are dropped. Field lists were taken
 * from a fake-mode server (`/openapi.json` and real responses, pinned in tests/fixtures.ts). */
import { z } from "zod";

const num = z.number();
const str = z.string();
const bool = z.boolean();

// ---- problems (RFC 9457) ----------------------------------------------------------------

export const ProblemSchema = z.object({
  type: str.optional(),
  title: str.optional(),
  status: num.optional(),
  detail: str.nullable().optional(),
  instance: str.nullable().optional(),
  errors: z.array(z.object({ pointer: str, detail: str })).nullable().optional(),
});

// ---- enums -------------------------------------------------------------------------------

export const DEPTHS = ["brief", "detailed", "technical"] as const;
export const DepthSchema = z.enum(DEPTHS);
export type Depth = z.infer<typeof DepthSchema>;

/** The sources a user can choose ("memory" exists in the API but is not a user choice). */
export const SOURCE_CHOICES = ["web", "wikipedia", "documents"] as const;
export type SourceChoice = (typeof SOURCE_CHOICES)[number];

export const RUN_STATUSES = [
  "queued",
  "running",
  "awaiting_approval",
  "completed",
  "failed",
  "cancelled",
] as const;
export const RunStatusSchema = z.enum(RUN_STATUSES);
export type RunStatus = z.infer<typeof RunStatusSchema>;
export const TERMINAL: ReadonlySet<RunStatus> = new Set(["completed", "failed", "cancelled"]);

export const TOOL_STATUSES = ["ok", "empty", "error", "refused", "capped"] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];

// ---- conversation history (discriminated on `action`) -----------------------------------

export const TokensSchema = z.object({ input: num, output: num, thinking: num.optional() });

const EntryBase = z.object({
  seq: num,
  iteration: num,
  agent: str, // display name: Supervisor, Researcher, …, Human, System
  model: str.nullable().optional(),
  llm_calls: num.optional().default(0),
  cached: bool.optional().default(false),
  duration_ms: num.nullable().optional(),
  tokens: TokensSchema.optional(),
});

export const DelegateEntrySchema = EntryBase.extend({
  action: z.literal("DELEGATE"),
  target_agent: str.nullable(),
  task: str.nullable(),
  delegations: z.array(z.object({ target_agent: str, task: str })),
  reason: str.nullable(),
  repaired: bool,
});
export const FinalEntrySchema = EntryBase.extend({
  action: z.literal("FINAL"),
  reason: str.nullable(),
  forced: bool,
});
export const ToolCallEntrySchema = EntryBase.extend({
  action: z.literal("TOOL_CALL"),
  tool: str,
  args: z.record(str, z.unknown()),
  status: str, // ok · empty · error · refused · capped (kept open: unknown ones render as text)
  provider: str.nullable(),
  fallback: str.nullable(),
  results: num,
  evidence: z.array(str),
  error: str.nullable(),
  weak: bool,
  repeat: bool,
});
export const ResultEntrySchema = EntryBase.extend({
  action: z.literal("RESULT"),
  task: str.nullable(),
  content: str,
  evidence: z.array(str),
  data: z.record(str, z.unknown()),
  failed: bool.optional().default(false),
  unsourced: bool.optional().default(false),
});
export const FeedbackEntrySchema = EntryBase.extend({ action: z.literal("FEEDBACK"), text: str.nullable() });
export const NoticeEntrySchema = EntryBase.extend({
  action: z.literal("NOTICE"),
  text: str.nullable(),
  flag: str.nullable(),
});

const KNOWN_ACTIONS = new Set(["DELEGATE", "FINAL", "TOOL_CALL", "RESULT", "FEEDBACK", "NOTICE"]);
/** Forward compatible: an action this UI does not know is kept as a generic entry. A known
 * action with a malformed shape still fails. */
export const OtherEntrySchema = EntryBase.extend({
  action: str.refine((a) => !KNOWN_ACTIONS.has(a)),
  text: str.nullable().optional(),
});

export const KnownEntrySchema = z.discriminatedUnion("action", [
  DelegateEntrySchema,
  FinalEntrySchema,
  ToolCallEntrySchema,
  ResultEntrySchema,
  FeedbackEntrySchema,
  NoticeEntrySchema,
]);
export const EntrySchema = z.union([KnownEntrySchema, OtherEntrySchema]);

export type DelegateEntry = z.infer<typeof DelegateEntrySchema>;
export type FinalEntry = z.infer<typeof FinalEntrySchema>;
export type ToolCallEntry = z.infer<typeof ToolCallEntrySchema>;
export type ResultEntry = z.infer<typeof ResultEntrySchema>;
export type FeedbackEntry = z.infer<typeof FeedbackEntrySchema>;
export type NoticeEntry = z.infer<typeof NoticeEntrySchema>;
export type OtherEntry = z.infer<typeof OtherEntrySchema>;
export type DecisionEntry = DelegateEntry | FinalEntry;
export type Entry = z.infer<typeof EntrySchema>;

/** Structured worker data, parsed where it is shown (lenient: extra keys dropped). */
export const ResearcherDataSchema = z.object({
  tool_calls: num.optional(),
  capped: bool.optional(),
  tools: z.record(str, num).optional(),
  queries: z.array(str).optional(),
  fallbacks: num.optional(),
  repeats: num.optional(),
  rewrites: num.optional(),
  summary: str.optional(),
});
export const ReviewDataSchema = z.object({
  verdict: str,
  issues: z.array(
    z.object({ severity: str, text: str, category: str.nullable().optional(), section: str.nullable().optional() }),
  ),
  balance_flags: z.array(str).optional().default([]),
});
export const FactCheckDataSchema = z.object({
  claims: z.array(
    z.object({
      sentence: str.optional(),
      claim: str.optional(),
      section: str.nullable().optional(),
      status: str,
      evidence_ids: z.array(str).optional().default([]),
      cited: z.array(str).optional(),
      note: str.nullable().optional(),
      miscited: bool.optional(),
    }),
  ),
});

// ---- report ------------------------------------------------------------------------------

export const ReportSourceSchema = z.object({
  id: str, // "E3"
  title: str,
  url: str.nullable(),
  document_name: str.nullable(),
  page: num.nullable(),
  source_kind: str, // web · wikipedia · documents · memory
  snippet: str.nullable().optional(),
});
export type ReportSource = z.infer<typeof ReportSourceSchema>;

export const ReportSchema = z.object({
  title: str,
  summary: str,
  sections: z.array(z.object({ heading: str, content: str })),
  references: z.array(str),
  sources: z.array(ReportSourceSchema).optional().default([]),
  depth: str,
  word_count: num,
});
export type Report = z.infer<typeof ReportSchema>;

// ---- runs --------------------------------------------------------------------------------

export const GroundingSchema = z.object({
  checked: num,
  supported: num,
  unsupported: num.optional().default(0),
  contradicted: num.optional().default(0),
  supported_ratio: num.optional(),
});

export const MetadataSchema = z.object({
  iterations: num,
  iteration_limit: num,
  max_iterations: num.optional(),
  agents_used: z.array(str),
  duration_seconds: num.nullable(),
  forced: bool,
  degraded: bool,
  llm_calls: num,
  cached_calls: num,
  tool_calls: num,
  evidence: num.optional().default(0),
  context_turns: num.optional().default(0), // v1.2: earlier turns shown to the agents
  tokens: z.object({ input: num, output: num }),
  est_cost_usd: num,
  per_agent: z.record(str, z.object({ calls: num, tokens: num, duration_ms: num })),
  grounding: GroundingSchema.nullable().optional().default(null),
  models: z.record(str, str.nullable()),
  prompt_version: str,
  generated_by: str,
  created_at: str,
  started_at: str.nullable().optional(),
  finished_at: str.nullable().optional(),
});
export type Metadata = z.infer<typeof MetadataSchema>;

/** v1.2 ids: `conv_` + 16 hex. */
export const CONVERSATION_ID = /^conv_[a-f0-9]{16}$/;

export const ResearchViewSchema = z.object({
  run_id: str,
  // v1.2. Optional: stored samples and older backends do not send them.
  conversation_id: str.optional(),
  turn: num.optional(),
  status: RunStatusSchema,
  question: str,
  depth: str,
  sources: z.array(str),
  channel: str,
  report: ReportSchema.nullable(),
  metadata: MetadataSchema,
  conversation_history: z.array(EntrySchema),
  final_output: str.nullable(),
  flags: z.array(str),
  memory_used: z.array(z.object({ run_id: str, question: str })),
  error: str.nullable(),
  approval: z.object({
    required: bool,
    draft: str.nullable(),
    expires_at: str.nullable(),
    revisions: num,
  }),
  sample: bool,
});
export type ResearchView = z.infer<typeof ResearchViewSchema>;

export const AcceptedSchema = z.object({
  run_id: str,
  status: str,
  status_url: str,
  events_url: str,
  web_url: str.optional(),
  conversation_id: str.optional(), // v1.2 (optional: older backends)
  turn: num.optional(),
});
export type Accepted = z.infer<typeof AcceptedSchema>;

/** `GET /conversations/{id}` (v1.2): the turns of a chat, oldest first. */
export const ConversationTurnSchema = z.object({
  run_id: str,
  turn: num,
  question: str,
  status: str, // a RunStatus; kept open
  title: str.nullable(),
  depth: str.optional(),
  created_at: str,
});
export const ConversationSchema = z.object({
  conversation_id: str,
  max_turns: num,
  turns: z.array(ConversationTurnSchema),
});
export type Conversation = z.infer<typeof ConversationSchema>;

/** `GET /tools`. The family stays open: "conversation" (v1.2, internal) and any future one
 * parse; only the known ones get a label. */
export const TOOL_FAMILIES = ["web", "wikipedia", "documents", "conversation"] as const;
export const ToolSpecSchema = z.object({
  name: str,
  family: str,
  description: str,
  parameters: z.record(str, z.unknown()).optional(),
});
export type ToolSpec = z.infer<typeof ToolSpecSchema>;

export const DeliverySchema = z.object({
  id: str,
  kind: str,
  event: str,
  target_host: str.nullable(),
  status: str,
  attempts: num,
  last_status: num.nullable(),
  last_error: str.nullable(),
  next_attempt_at: str.nullable(),
  delivered_at: str.nullable(),
});
export type Delivery = z.infer<typeof DeliverySchema>;

// ---- documents ---------------------------------------------------------------------------

export const DOC_STATUSES = ["queued", "indexing", "ready", "failed"] as const;
export const DocumentSchema = z.object({
  id: str,
  name: str,
  media_type: str,
  size_bytes: num,
  status: str,
  pages: num.nullable(),
  chunks: num.nullable(),
  error: str.nullable(),
  created_at: str,
  expires_at: str.nullable(),
  demo: bool,
});
export type DocumentView = z.infer<typeof DocumentSchema>;

/** The workspace summary: null until the first upload. */
export const WorkspaceInfoSchema = z.object({
  id: str,
  created_at: str.nullable().optional(),
  expires_at: str.nullable().optional(),
  bytes_used: num,
  documents: num,
  limits: z.object({ max_documents: num, max_bytes: num }).partial().optional(),
});
export type WorkspaceInfo = z.infer<typeof WorkspaceInfoSchema>;

export const DocumentListSchema = z.object({
  workspace: WorkspaceInfoSchema.nullable().optional(),
  documents: z.array(DocumentSchema),
  demo: z.array(DocumentSchema),
});
export type DocumentList = z.infer<typeof DocumentListSchema>;

// ---- server info -------------------------------------------------------------------------

export const LimitsSchema = z.object({
  question_min_chars: num,
  question_max_chars: num,
  default_max_iterations: num,
  max_iterations_limit: num,
  max_parallel_delegations: num.optional(),
  approval_timeout_min: num.optional(),
  feedback_max_chars: num,
  // v1.2 (not sent by /health yet: the UI falls back to the contract values 2 and 30)
  followup_min_chars: num.optional(),
  conversation_max_turns: num.optional(),
  // Sent since the frontend merge; optional so older backends still parse.
  upload_max_bytes: num.optional(),
  workspace_max_docs: num.optional(),
  workspace_max_bytes: num.optional(),
});
export type Limits = z.infer<typeof LimitsSchema>;

export const HealthSchema = z.object({
  status: str,
  service_level: str, // full · reduced · minimal
  service_reason: str.nullable(),
  retry_after_s: num.nullable(),
  llm_mode: str,
  wikipedia: str.optional(),
  search: z.object({ mode: str, available: bool }).optional(),
  embedder: z.object({ mode: str, status: str }).optional(),
  telegram: z.object({ mode: str, configured: bool }).partial().optional(),
  outbox: z.object({ pending: num, dead: num }).partial().optional(),
  models: z.record(str, str.nullable()),
  prompt_version: str,
  usage_today: z.object({ calls: num, limit: num }),
  limits: LimitsSchema,
});
export type Health = z.infer<typeof HealthSchema>;

export const AgentSchema = z.object({
  name: str,
  title: str,
  role: str,
  description: str,
});
export type Agent = z.infer<typeof AgentSchema>;

export const SampleSchema = z.object({ id: str, title: str, question: str });
export type Sample = z.infer<typeof SampleSchema>;

// ---- SSE events ----------------------------------------------------------------------------

export const EventSchemas = {
  run_started: z.object({ iteration: num, iteration_limit: num }),
  agent_started: z.object({ agent: str, iteration: num, task: str.nullable() }),
  decision: z.discriminatedUnion("action", [DelegateEntrySchema, FinalEntrySchema]),
  tool_call: ToolCallEntrySchema,
  worker_result: ResultEntrySchema,
  feedback: FeedbackEntrySchema,
  notice: NoticeEntrySchema,
  evidence_added: z.object({ count: num, total: num }),
  awaiting_approval: z.object({ draft: str, expires_at: str.nullable() }),
  report_ready: z.object({ title: str, sections: num }),
  completed: z.object({ final_output: str.nullable(), forced: bool, degraded: bool }),
  failed: z.object({ error: str.nullable() }),
  cancelled: z.object({ reason: str.nullable() }),
} as const;

export type EventType = keyof typeof EventSchemas;
export const EVENT_TYPES = Object.keys(EventSchemas) as EventType[];
export const TERMINAL_EVENTS: ReadonlySet<EventType> = new Set(["completed", "failed", "cancelled"]);
/** Events that carry a conversation_history entry. */
export const ENTRY_EVENTS: ReadonlySet<EventType> = new Set([
  "decision",
  "tool_call",
  "worker_result",
  "feedback",
  "notice",
]);

export type RunEvent = {
  [K in EventType]: { id: number; type: K; data: z.infer<(typeof EventSchemas)[K]> };
}[EventType];
