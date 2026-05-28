"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

import { cn } from "@/lib/utils";

/**
 * Themed read-only Markdown renderer used by the Skills surface.
 *
 * Pipeline:
 * - ``remark-gfm`` enables tables, strikethrough, task lists, autolinks.
 * - ``rehype-sanitize`` strips any HTML-via-markdown that would let a
 *   crafted skill body inject script / iframe / style. Sanitisation is
 *   non-negotiable here: skill bodies originate from operator input.
 *
 * Styling is fully driven by the GitHub Dark CSS variables already
 * declared in ``globals.css`` (--foreground, --background-elevated,
 * --border) — no Tailwind typography plugin, no @tailwindcss/typography.
 */
export interface MarkdownViewProps {
  source: string;
  className?: string;
  "aria-label"?: string;
  "data-testid"?: string;
}

export function MarkdownView({
  source,
  className,
  ...rest
}: MarkdownViewProps): React.JSX.Element {
  return (
    <div
      className={cn(
        // Prose-like vertical rhythm; uses :where()-style descendant
        // selectors so we don't have to wrap each child in a Tailwind
        // class.
        "markdown-view text-sm leading-relaxed text-[rgb(var(--foreground))]",
        "[&_h1]:mb-3 [&_h1]:mt-4 [&_h1]:text-xl [&_h1]:font-semibold [&_h1]:tracking-tight",
        "[&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:tracking-tight",
        "[&_h3]:mb-2 [&_h3]:mt-3 [&_h3]:text-base [&_h3]:font-semibold",
        "[&_p]:my-2",
        "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6",
        "[&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-6",
        "[&_li]:my-1",
        "[&_a]:text-[rgb(var(--accent))] [&_a]:underline-offset-2 hover:[&_a]:underline",
        "[&_strong]:font-semibold",
        "[&_em]:italic",
        "[&_hr]:my-4 [&_hr]:border-[rgb(var(--border))]",
        "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-[rgb(var(--border))] [&_blockquote]:pl-3 [&_blockquote]:text-[rgb(var(--foreground-muted))]",
        "[&_code]:rounded-sm [&_code]:bg-[rgb(var(--background-elevated))] [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs",
        "[&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-[rgb(var(--border))] [&_pre]:bg-[rgb(var(--background-elevated))] [&_pre]:p-3 [&_pre]:font-mono [&_pre]:text-xs",
        "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
        "[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:border [&_table]:border-[rgb(var(--border))]",
        "[&_th]:border [&_th]:border-[rgb(var(--border))] [&_th]:bg-[rgb(var(--background-elevated))] [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold",
        "[&_td]:border [&_td]:border-[rgb(var(--border))] [&_td]:px-2 [&_td]:py-1",
        className,
      )}
      aria-label={rest["aria-label"]}
      data-testid={rest["data-testid"]}
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
      >
        {source}
      </Markdown>
    </div>
  );
}

export default MarkdownView;
