import { useSyncExternalStore } from "react";
import { getWorkspaceId } from "@/lib/workspace";

const noop = () => () => {};

/** This browser's workspace id; null during server rendering (no storage there). */
export function useWorkspace(): string | null {
  return useSyncExternalStore(noop, getWorkspaceId, () => null);
}
