/** The browser's private workspace (BRAINSTORM: a workspace per browser, 24 h): a lowercase
 * UUIDv4 generated once and kept in localStorage. It is sent as X-Workspace-Id with every run
 * and upload, so `search_documents` sees this browser's uploads plus the demo library.
 * Storage can be blocked (private mode): the id then lives for the page's lifetime only. */

export const WORKSPACE_KEY = "research-workspace-v1";
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

let memory: string | null = null;

export function isWorkspaceId(value: unknown): value is string {
  return typeof value === "string" && UUID_V4.test(value);
}

export function newWorkspaceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().toLowerCase();
  }
  // RFC 4122 §4.4 from getRandomValues (older browsers without randomUUID)
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** The workspace id, created on first use. */
export function getWorkspaceId(): string {
  try {
    const saved = storage()?.getItem(WORKSPACE_KEY);
    if (isWorkspaceId(saved)) return saved;
  } catch {
    // blocked storage: fall through to the in-memory id
  }
  if (!memory) memory = newWorkspaceId();
  try {
    storage()?.setItem(WORKSPACE_KEY, memory);
  } catch {
    // not remembered across reloads
  }
  return memory;
}

/** Tests: forget the in-memory id. */
export function resetWorkspaceMemory(): void {
  memory = null;
}
