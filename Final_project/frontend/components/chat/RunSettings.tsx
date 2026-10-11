import { DEPTH_PROFILES, SOURCE_LABELS } from "@/lib/format";
import { DEPTHS, type Depth, SOURCE_CHOICES, type SourceChoice } from "@/lib/schemas";

export interface Settings {
  depth: Depth;
  sources: SourceChoice[];
  max_iterations: number | null; // null: the depth's default
  require_approval: boolean;
  use_memory: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  depth: "detailed",
  sources: ["web", "wikipedia", "documents"],
  max_iterations: null,
  require_approval: false,
  use_memory: true,
};

const SOURCE_HINTS: Record<SourceChoice, string> = {
  web: "SearXNG web search",
  wikipedia: "Wikipedia articles",
  documents: "your uploads + the demo library",
};

/** Depth (segmented control), sources (at least one) and the advanced options (collapsed). */
export default function RunSettings({
  value,
  maxIterations,
  searchAvailable,
  indexWarming,
  onChange,
}: {
  value: Settings;
  maxIterations: number;
  searchAvailable: boolean;
  indexWarming: boolean;
  onChange: (value: Settings) => void;
}) {
  const toggleSource = (s: SourceChoice, on: boolean) => {
    const next = on ? [...new Set([...value.sources, s])] : value.sources.filter((x) => x !== s);
    onChange({ ...value, sources: SOURCE_CHOICES.filter((x) => next.includes(x)) });
  };
  const advanced = [
    value.max_iterations ? `${value.max_iterations} decisions max` : "decisions: by depth",
    value.require_approval ? "approval on" : "approval off",
    value.use_memory ? "memory on" : "memory off",
  ].join(" · ");
  const profile = DEPTH_PROFILES[value.depth];

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-x-6 gap-y-3">
        <fieldset className="min-w-0">
          <legend className="mb-1 font-medium">Depth</legend>
          <div className="inline-flex flex-wrap overflow-hidden rounded-md border border-zinc-400 dark:border-zinc-600">
            {DEPTHS.map((d) => (
              <label
                key={d}
                className={`cursor-pointer px-3 py-1 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-orange-700 ${value.depth === d ? "bg-orange-700 text-white" : "bg-white text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200"}`}
              >
                <input
                  type="radio"
                  name="depth"
                  value={d}
                  checked={value.depth === d}
                  onChange={() => onChange({ ...value, depth: d })}
                  className="sr-only"
                />
                {DEPTH_PROFILES[d].label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
            {profile.words[0]}–{profile.words[1]} words · {profile.sections[0]}–{profile.sections[1]} sections
          </p>
        </fieldset>
        <fieldset className="min-w-0">
          <legend className="mb-1 font-medium">Sources</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {SOURCE_CHOICES.map((s) => (
              <label key={s} className="flex items-center gap-1.5" title={SOURCE_HINTS[s]}>
                <input type="checkbox" checked={value.sources.includes(s)} onChange={(e) => toggleSource(s, e.target.checked)} />
                {SOURCE_LABELS[s]}
              </label>
            ))}
          </div>
          {value.sources.length === 0 && (
            <p className="mt-1 text-xs text-red-800 dark:text-red-300">Choose at least one source.</p>
          )}
          {!searchAvailable && value.sources.includes("web") && (
            <p className="mt-1 text-xs text-amber-900 dark:text-amber-200">
              Web search is unavailable right now
              {value.sources.includes("wikipedia") ? ": the Researcher falls back to Wikipedia." : ": the run may find nothing on the web."}
            </p>
          )}
          {indexWarming && value.sources.includes("documents") && (
            <p className="mt-1 text-xs text-amber-900 dark:text-amber-200">The document index is warming up.</p>
          )}
        </fieldset>
      </div>
      <details>
        <summary className="cursor-pointer text-zinc-700 dark:text-zinc-300">Advanced: {advanced}</summary>
        <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2">
          <label className="flex items-center gap-2">
            Max decisions
            <select
              value={value.max_iterations ?? ""}
              onChange={(e) => onChange({ ...value, max_iterations: e.target.value ? Number(e.target.value) : null })}
              className="rounded border border-zinc-400 bg-white px-1 py-0.5 dark:border-zinc-600 dark:bg-zinc-900"
            >
              <option value="">By depth</option>
              {Array.from({ length: maxIterations }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={value.require_approval}
              onChange={(e) => onChange({ ...value, require_approval: e.target.checked })}
            />
            Require my approval before the final report
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={value.use_memory} onChange={(e) => onChange({ ...value, use_memory: e.target.checked })} />
            Use memory of earlier reports
          </label>
        </div>
      </details>
    </div>
  );
}
