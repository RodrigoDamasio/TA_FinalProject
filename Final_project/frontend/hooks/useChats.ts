import { useSyncExternalStore } from "react";
import { type ChatState, EMPTY, loadChats, saveChats } from "@/lib/chats";
import { loadOverrides, type Overrides, saveOverrides } from "@/lib/collapse";

/** A browser-storage value as an external store: the server renders the empty value and the
 * browser its saved one, without a hydration mismatch or a setState in an effect. */
function createStore<T>(load: () => T, save: (value: T) => void) {
  let cache: T | null = null;
  const listeners = new Set<() => void>();
  return {
    get(): T {
      if (cache === null) cache = load();
      return cache;
    },
    set(value: T): void {
      cache = value;
      save(value);
      listeners.forEach((l) => l());
    },
    /** Applies a change to the latest value (several changes in one event stay consistent). */
    update(change: (value: T) => T): void {
      this.set(change(this.get()));
    },
    subscribe(listener: () => void): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Tests: forget the cached snapshot. */
    reset(): void {
      cache = null;
    },
  };
}

export const chatStore = createStore<ChatState>(loadChats, saveChats);
export const collapseStore = createStore<Overrides>(loadOverrides, saveOverrides);

const NO_OVERRIDES: Overrides = {};

export function useChats(): ChatState {
  return useSyncExternalStore(chatStore.subscribe, () => chatStore.get(), () => EMPTY);
}

export function useCollapse(): Overrides {
  return useSyncExternalStore(collapseStore.subscribe, () => collapseStore.get(), () => NO_OVERRIDES);
}
