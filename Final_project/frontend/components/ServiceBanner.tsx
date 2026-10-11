"use client";
import { useCountdown } from "@/hooks/useCountdown";
import type { ServiceLevel } from "@/hooks/useHealth";
import { formatDuration } from "@/lib/format";
import type { Health } from "@/lib/schemas";

/** Graceful degradation made visible (course §1.4): the service level, the search and
 * embedder status, and the demo-mode label. Nothing alarming is shown at "full". */
export default function ServiceBanner({
  level,
  health,
  retryAt,
}: {
  level: ServiceLevel;
  health: Health | null;
  retryAt: number | null; // ms timestamp of the quota reset
}) {
  const left = useCountdown(level === "minimal" ? retryAt : null);
  const demo = health?.llm_mode === "fake";
  const notes: string[] = [];
  if (health?.search && !health.search.available) notes.push("Web search is unavailable: the Researcher falls back to Wikipedia.");
  if (health?.embedder?.status === "loading") notes.push("The document index is warming up.");
  if (health?.embedder?.status === "failed" || health?.embedder?.status === "unavailable") {
    notes.push("Document search is unavailable right now.");
  }

  const text =
    level === "reduced"
      ? `Some sources are unavailable${health?.service_reason ? ` (${health.service_reason})` : ""}: the tools fall back and say so.`
      : level === "minimal"
        ? `Today's free AI quota is used up. The samples still work.${left > 0 ? ` New runs open again in ${formatDuration(left)}.` : ""}`
        : level === "offline"
          ? "The research service can't be reached right now. Try again in a minute."
          : null;

  if (!demo && !text && notes.length === 0) return null;
  return (
    <div role="status" aria-label="Service status" className="space-y-1">
      {demo && (
        <p className="rounded-md border border-sky-700 bg-sky-50 px-2 py-1 text-xs text-sky-900 dark:bg-sky-950 dark:text-sky-100">
          <span className="font-medium">Demo mode:</span> the agents are simulated (no Gemini calls); search results
          are canned.
        </p>
      )}
      {text && (
        <p className="rounded-md border border-amber-500 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          <span className="font-medium">Service {level}:</span> {text}
        </p>
      )}
      {notes.map((n) => (
        <p key={n} className="rounded-md border border-amber-500 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          {n}
        </p>
      ))}
    </div>
  );
}
