"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Badge, { type Tone } from "@/components/Badge";
import ErrorBanner from "@/components/ErrorBanner";
import ServiceBanner from "@/components/ServiceBanner";
import { useApiError } from "@/hooks/useApiError";
import { useHealth } from "@/hooks/useHealth";
import { useWorkspace } from "@/hooks/useWorkspace";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { DocumentView, WorkspaceInfo } from "@/lib/schemas";

export const POLL_MS = 2000;
const ACCEPT = ".md,.txt,.pdf,text/markdown,text/plain,application/pdf";
const EXTENSIONS = [".md", ".txt", ".pdf"];
// Backend defaults (UPLOAD_MAX_BYTES, WORKSPACE_MAX_DOCS, WORKSPACE_MAX_BYTES). The workspace
// summary of GET /documents (after the first upload) and /health.limits override them.
const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;
const DEFAULT_MAX_DOCS = 10;
const DEFAULT_MAX_TOTAL = 20 * 1024 * 1024;

const STATUS_TONE: Record<string, Tone> = { ready: "good", failed: "bad", indexing: "accent", queued: "neutral" };
const PENDING = new Set(["queued", "indexing"]);

interface Upload {
  key: number;
  name: string;
  progress: number; // 0..1
  error: string | null;
}

const TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "text/markdown": "Markdown",
  "text/plain": "Text",
};

/** Checks a file before upload (the backend checks again, by content). */
export function checkFile(
  file: File,
  docs: DocumentView[],
  limits: { maxBytes: number; maxDocs: number; maxTotal: number },
): string | null {
  const name = file.name.toLowerCase();
  if (!EXTENSIONS.some((e) => name.endsWith(e))) return "Only .md, .txt and .pdf files are accepted.";
  if (file.size === 0) return "The file is empty.";
  if (file.size > limits.maxBytes) return `The file is larger than ${formatBytes(limits.maxBytes)}.`;
  if (docs.length >= limits.maxDocs) return `Your workspace already has ${limits.maxDocs} documents. Delete one first.`;
  const total = docs.reduce((sum, d) => sum + d.size_bytes, 0);
  if (total + file.size > limits.maxTotal) return `Your workspace would exceed ${formatBytes(limits.maxTotal)}.`;
  return null;
}

function DocumentRow({
  doc,
  confirming,
  busy,
  onAskDelete,
  onCancel,
  onDelete,
}: {
  doc: DocumentView;
  confirming: boolean;
  busy: boolean;
  onAskDelete: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const facts = [
    TYPE_LABELS[doc.media_type] ?? doc.media_type,
    formatBytes(doc.size_bytes),
    doc.pages !== null ? `${doc.pages} pages` : null,
    doc.chunks ? `${doc.chunks} chunks` : null,
  ].filter(Boolean);
  return (
    <li className="space-y-1 rounded-md border border-zinc-300 p-2 dark:border-zinc-700" aria-label={doc.name} data-status={doc.status}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 font-medium break-all">{doc.name}</span>
        <Badge tone={STATUS_TONE[doc.status] ?? "neutral"}>{doc.status}</Badge>
        {doc.demo && <Badge>demo</Badge>}
      </div>
      <p className="text-xs text-zinc-700 dark:text-zinc-300">
        {facts.join(" · ")}
        {doc.expires_at ? ` · expires ${formatDate(doc.expires_at)}` : ""}
      </p>
      {doc.error && <p className="text-xs text-red-800 dark:text-red-300">{doc.error}</p>}
      {!doc.demo &&
        (confirming ? (
          <div className="flex flex-wrap items-center gap-3 text-sm" role="group" aria-label={`Confirm deleting ${doc.name}`}>
            <span>Delete {doc.name}?</span>
            <button type="button" disabled={busy} onClick={onDelete} className="rounded bg-red-700 px-2 py-0.5 font-medium text-white disabled:opacity-60">
              Delete
            </button>
            <button type="button" onClick={onCancel} className="font-medium underline">
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={onAskDelete}
            disabled={PENDING.has(doc.status)}
            aria-label={`Delete ${doc.name}`}
            className="text-sm font-medium text-red-800 underline disabled:text-zinc-500 dark:text-red-300"
          >
            Delete
          </button>
        ))}
    </li>
  );
}

/** The document library (FRONTEND_PLAN §3.6): this browser's uploads and the demo library. */
export default function LibraryApp() {
  const workspaceId = useWorkspace();
  const { health, level, retryAt } = useHealth();
  const { error, cooldown, fail, clear } = useApiError();
  const [docs, setDocs] = useState<DocumentView[] | null>(null);
  const [demo, setDemo] = useState<DocumentView[]>([]);
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const counter = useRef(0);
  const input = useRef<HTMLInputElement>(null);

  const limits = {
    maxBytes: health?.limits.upload_max_bytes ?? DEFAULT_MAX_BYTES,
    maxDocs: workspace?.limits?.max_documents ?? health?.limits.workspace_max_docs ?? DEFAULT_MAX_DOCS,
    maxTotal: workspace?.limits?.max_bytes ?? health?.limits.workspace_max_bytes ?? DEFAULT_MAX_TOTAL,
  };

  const load = useCallback(() => {
    if (!workspaceId) return;
    api.listDocuments(workspaceId).then(
      (list) => {
        setDocs(list.documents);
        setDemo(list.demo);
        setWorkspace(list.workspace ?? null);
      },
      (err) => {
        setDocs([]);
        fail(err);
      },
    );
  }, [workspaceId, fail]);

  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);

  // ---- status polling: every 2 s while a document is queued or indexing --------------------
  const pendingIds = (docs ?? []).filter((d) => PENDING.has(d.status)).map((d) => d.id).join(",");
  useEffect(() => {
    if (!pendingIds || !workspaceId) return;
    const timer = setInterval(() => {
      for (const id of pendingIds.split(",")) {
        api.getDocument(id, workspaceId).then(
          (d) => setDocs((all) => (all ?? []).map((x) => (x.id === d.id ? d : x))),
          (err) => {
            if (err instanceof ApiError && err.kind === "not_found") {
              setDocs((all) => (all ?? []).filter((x) => x.id !== id));
            }
          },
        );
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [pendingIds, workspaceId]);

  // ---- upload -------------------------------------------------------------------------------
  async function uploadOne(file: File, current: DocumentView[]) {
    const key = ++counter.current;
    const problem = checkFile(file, current, limits);
    setUploads((u) => [...u, { key, name: file.name, progress: 0, error: problem }]);
    if (problem || !workspaceId) return null;
    try {
      const doc = await api.uploadDocument(file, workspaceId, (progress) =>
        setUploads((u) => u.map((x) => (x.key === key ? { ...x, progress } : x))),
      );
      setUploads((u) => u.filter((x) => x.key !== key));
      setDocs((all) => [doc, ...(all ?? []).filter((d) => d.id !== doc.id)]);
      return doc;
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "The upload failed.";
      setUploads((u) => u.map((x) => (x.key === key ? { ...x, error: message } : x)));
      if (err instanceof ApiError && err.retryAfter) fail(err);
      return null;
    }
  }

  async function uploadAll(files: FileList | File[]) {
    clear();
    let current = docs ?? [];
    for (const file of Array.from(files)) {
      const doc = await uploadOne(file, current);
      if (doc) current = [doc, ...current.filter((d) => d.id !== doc.id)];
    }
    if (input.current) input.current.value = "";
  }

  async function remove(id: string) {
    if (!workspaceId) return;
    setDeleting(true);
    clear();
    try {
      await api.deleteDocument(id, workspaceId);
      setDocs((all) => (all ?? []).filter((d) => d.id !== id));
      setConfirming(null);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "not_found") {
        setDocs((all) => (all ?? []).filter((d) => d.id !== id));
        setConfirming(null);
      } else {
        fail(err);
      }
    } finally {
      setDeleting(false);
    }
  }

  const rows = (list: DocumentView[]) =>
    list.map((d) => (
      <DocumentRow
        key={d.id}
        doc={d}
        confirming={confirming === d.id}
        busy={deleting}
        onAskDelete={() => setConfirming(d.id)}
        onCancel={() => setConfirming(null)}
        onDelete={() => remove(d.id)}
      />
    ));

  return (
    <div className="flex flex-col gap-6">
      <ServiceBanner level={level} health={health} retryAt={retryAt} />
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">Document library</h2>
        <p className="text-sm text-zinc-700 dark:text-zinc-300">
          Upload .md, .txt or .pdf files (up to {formatBytes(limits.maxBytes)} each, {limits.maxDocs} documents and{" "}
          {formatBytes(limits.maxTotal)} per workspace). The Researcher searches them with{" "}
          <code className="font-mono">search_documents</code> when “Documents” is a source.
        </p>
        <p className="text-xs text-zinc-600 dark:text-zinc-400">
          Uploads are private to this browser and expire after 24 hours. Their text is sent to Gemini as data, never as
          instructions.
        </p>
      </div>

      <section aria-label="Upload" className="space-y-2">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length) uploadAll(e.dataTransfer.files);
          }}
          className={`rounded-lg border-2 border-dashed p-4 text-center text-sm ${dragging ? "border-orange-700 bg-orange-50 dark:bg-orange-950" : "border-zinc-400 dark:border-zinc-600"}`}
        >
          <p>Drag and drop files here, or</p>
          <label className="mt-2 inline-block cursor-pointer rounded-md bg-orange-700 px-3 py-1.5 font-medium text-white hover:bg-orange-800 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-orange-700">
            Choose files
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              disabled={!workspaceId}
              onChange={(e) => e.target.files && uploadAll(e.target.files)}
              className="sr-only"
            />
          </label>
        </div>
        {uploads.length > 0 && (
          <ul aria-label="Uploads" className="space-y-1 text-sm">
            {uploads.map((u) => (
              <li key={u.key} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 break-all">{u.name}</span>
                {u.error ? (
                  <>
                    <span className="text-red-800 dark:text-red-300" role="alert">
                      {u.error}
                    </span>
                    <button
                      type="button"
                      className="text-xs underline"
                      onClick={() => setUploads((all) => all.filter((x) => x.key !== u.key))}
                    >
                      Dismiss
                    </button>
                  </>
                ) : (
                  <progress max={1} value={u.progress} aria-label={`Uploading ${u.name}`} className="h-2 w-32" />
                )}
              </li>
            ))}
          </ul>
        )}
        {error && <ErrorBanner error={error} cooldown={cooldown} />}
      </section>

      <section aria-label="Your documents" className="space-y-2">
        <h3 className="text-sm font-semibold tracking-wide uppercase">Your documents</h3>
        {docs === null ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
        ) : docs.length === 0 ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">No uploads yet.</p>
        ) : (
          <ul className="space-y-2">{rows(docs)}</ul>
        )}
      </section>

      <section aria-label="Demo library" className="space-y-2">
        <h3 className="text-sm font-semibold tracking-wide uppercase">Demo library (read-only)</h3>
        {demo.length === 0 ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">No demo documents.</p>
        ) : (
          <ul className="space-y-2">{rows(demo)}</ul>
        )}
      </section>
    </div>
  );
}
