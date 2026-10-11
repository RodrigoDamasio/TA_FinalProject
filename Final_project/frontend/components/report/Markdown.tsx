import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CITE_PREFIX, linkCitations, safeUrl } from "@/lib/citations";
import { domain } from "@/lib/format";
import type { ReportSource } from "@/lib/schemas";
import CitationChip from "./CitationChip";

/** Model output is untrusted: no raw HTML, no images, only http(s) links (domain shown),
 * and [E<n>] citations turned into chips when the evidence id is known. */
export default function Markdown({
  text,
  sources = [],
  runId,
  className = "",
}: {
  text: string;
  sources?: ReportSource[];
  runId: string;
  className?: string;
}) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const linked = linkCitations(text, new Set(byId.keys()));

  const components: Components = {
    a({ href, children }) {
      if (!href) return <span>{children}</span>;
      if (href.startsWith(CITE_PREFIX)) {
        const id = href.slice(CITE_PREFIX.length);
        const source = byId.get(id);
        if (source) return <CitationChip id={id} source={source} runId={runId} />;
        return <span>[{id}]</span>;
      }
      if (href.startsWith("#")) return <span>{children}</span>; // no in-page links from the model
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-orange-800 underline dark:text-orange-300">
          {children}
          <span className="text-xs text-zinc-600 dark:text-zinc-400"> ({domain(href)})</span>
        </a>
      );
    },
    img({ alt }) {
      return <span className="text-zinc-600 italic dark:text-zinc-400">[image{alt ? `: ${alt}` : ""}]</span>;
    },
    table({ children }) {
      return (
        <div role="region" aria-label="Table" tabIndex={0} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">{children}</table>
        </div>
      );
    },
    pre({ children }) {
      return (
        <pre tabIndex={0} className="my-3 overflow-x-auto rounded-md bg-zinc-100 p-3 text-sm dark:bg-zinc-900">
          {children}
        </pre>
      );
    },
  };

  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrl} components={components}>
        {linked}
      </ReactMarkdown>
    </div>
  );
}
