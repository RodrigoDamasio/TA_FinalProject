/** Which turns are expanded (plan Improving_Chat_UI_UX §8.2). By default only the latest turn
 * of a chat is; the user's choices override that per run, for this tab (sessionStorage). */
import { z } from "zod";

export const COLLAPSE_KEY = "research-collapse-v1";

export type Overrides = Record<string, boolean>; // run_id → expanded

function storage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

export function loadOverrides(): Overrides {
  try {
    const raw = storage()?.getItem(COLLAPSE_KEY);
    if (!raw) return {};
    const parsed = z.record(z.string(), z.boolean()).safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : {};
  } catch {
    return {};
  }
}

export function saveOverrides(overrides: Overrides): void {
  try {
    storage()?.setItem(COLLAPSE_KEY, JSON.stringify(overrides));
  } catch {
    // Blocked storage: the choice lasts until the page is reloaded.
  }
}

export function isExpanded(overrides: Overrides, runId: string, latest: boolean): boolean {
  return overrides[runId] ?? latest;
}

/** Sets every given run to one state (Collapse all / Expand all, or one toggle). */
export function setAll(overrides: Overrides, runIds: readonly string[], expanded: boolean): Overrides {
  const next = { ...overrides };
  for (const id of runIds) next[id] = expanded;
  return next;
}

/** Back to the default for these runs: a new question collapses every earlier turn. */
export function resetRuns(overrides: Overrides, runIds: readonly string[]): Overrides {
  const next = { ...overrides };
  for (const id of runIds) delete next[id];
  return next;
}
