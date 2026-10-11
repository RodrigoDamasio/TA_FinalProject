/** Display formatting for durations, costs, sizes, domains and text. */
import type { Depth } from "./schemas";

export function formatMs(ms: number): string {
  if (ms >= 60_000) return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms)} ms`;
}

/** Seconds → "45 s", "2 min 5 s", "about 5 h". */
export function formatDuration(seconds: number): string {
  if (seconds >= 3600) return `about ${Math.round(seconds / 3600)} h`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  return `${seconds} s`;
}

/** 0.002825 → "$0.0028"; 0 → "$0". */
export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "< $0.0001";
  return `$${usd.toFixed(4)}`;
}

/** 182344 → "178 KB". */
export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** "https://en.wikipedia.org/wiki/X" → "en.wikipedia.org"; invalid → "". */
export function domain(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

export function shorten(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** An ISO date as a short local date and time; "" when invalid. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** "just now", "5 min ago", "3 h ago", "2 d ago"; the date after a week; "" when invalid or
 * when there is no clock yet (`now` null during hydration). */
export function formatRelative(iso: string | null | undefined, now: number | null): string {
  if (!iso || now === null) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86_400) return `${Math.floor(s / 86_400)} d ago`;
  return formatDate(iso);
}

const FLAG_LABELS: Record<string, string> = {
  forced: "forced completion",
  degraded: "assembled automatically",
  unsourced: "unsourced research",
  possible_injection: "possible prompt injection",
  redacted: "sensitive data redacted",
  unknown_citation: "invalid citations removed",
  truncated: "an answer was cut off",
  canary_leak: "instructions leak blocked",
  cap_reached: "tool-call cap reached",
  memory: "memory used",
  protocol: "protocol error",
  fact_check_degraded: "fact check degraded",
};

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replaceAll("_", " ");
}

/** The depth profiles (backend `domain/depth.py`): word and section ranges. */
export const DEPTH_PROFILES: Record<Depth, { label: string; words: [number, number]; sections: [number, number] }> = {
  brief: { label: "Brief", words: [200, 400], sections: [2, 3] },
  detailed: { label: "Detailed", words: [600, 1000], sections: [4, 6] },
  technical: { label: "Technical", words: [800, 1400], sections: [5, 7] },
};

export const SOURCE_LABELS: Record<string, string> = {
  web: "Web",
  wikipedia: "Wikipedia",
  documents: "Documents",
  memory: "Memory",
  conversation: "Earlier turns", // v1.2 family, internal (never a user choice)
};

export const SOURCE_ICONS: Record<string, string> = {
  web: "🌐",
  wikipedia: "📚",
  documents: "📄",
  memory: "🧠",
  conversation: "💬",
};
