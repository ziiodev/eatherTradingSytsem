"use client";

/**
 * CodeMirror 6 wrapper used as the load-bearing editor for `agents.logica`.
 *
 * Owned by the `agents-crud` change. Imported (named export) by:
 *  - `app/(dashboard)/agentes/[id]/page.tsx`   — agent detail
 *  - `app/(dashboard)/skills/...`              — landing later in skills-catalog
 *
 * Design decisions (locked in `sdd/agents-crud/design`):
 *  - Library: `@uiw/react-codemirror` + `@codemirror/lang-python`. NOT Monaco
 *    (bundle size + worker shim cost under Next.js 16 App Router).
 *  - Theme: a CSS-vars-driven custom theme that reads `--background`,
 *    `--foreground`, `--border`, `--accent` from `globals.css`. This way
 *    the editor stays consistent with the GitHub Dark palette without us
 *    forking the palette into a separate file.
 *  - Client Component only — CodeMirror touches `window` and the DOM, and
 *    React 19 RSC would crash on `useEffect`-style internal hooks.
 *
 * Stable contract — must NOT change without a coordinated change to
 * dependent consumers:
 *  - Component name: `CodeMirrorEditor`
 *  - Props:
 *      value: string                       — current editor text
 *      onChange: (next: string) => void    — fires on every edit
 *      language?: "python"                 — default and currently only option
 *      height?: string                     — CSS height (default "100%")
 *      readOnly?: boolean
 *      "aria-label"?: string
 *      "data-testid"?: string
 *  - Default export and named export both available.
 */

import CodeMirror, { EditorView } from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import {
  HighlightStyle,
  syntaxHighlighting,
  syntaxTree,
} from "@codemirror/language";
import { RangeSetBuilder } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { useMemo } from "react";

export interface CodeMirrorEditorProps {
  value: string;
  onChange: (next: string) => void;
  language?: "python" | "markdown";
  height?: string;
  readOnly?: boolean;
  className?: string;
  "aria-label"?: string;
  "data-testid"?: string;
}

/**
 * Build the CodeMirror theme from CSS variables read from the document
 * root. We do this with `EditorView.theme(...)` and `var(--token)` so a
 * future theme switch (e.g. light mode) becomes a single CSS edit, not a
 * code edit.
 */
/**
 * GitHub-Dark-aligned syntax highlight palette. Tokens map to lezer tags
 * which `@codemirror/lang-{python,markdown}` emit when parsing. Without
 * this, the editor parses the source but every token renders in the base
 * foreground color (i.e. no visible "syntax highlighting"). Colors come
 * from the theme CSS vars where possible (`--accent` for headings/links)
 * and from explicit GH-Dark palette hex otherwise.
 */
const highlightStyle = HighlightStyle.define([
  // ── Markdown ──────────────────────────────────────────────────
  { tag: t.heading, color: "rgb(var(--accent))", fontWeight: "700" },
  { tag: t.heading1, color: "rgb(var(--accent))", fontWeight: "700", fontSize: "1.15em" },
  { tag: t.heading2, color: "rgb(var(--accent))", fontWeight: "700", fontSize: "1.08em" },
  { tag: t.heading3, color: "rgb(var(--accent))", fontWeight: "600" },
  { tag: t.strong, color: "rgb(var(--foreground))", fontWeight: "700" },
  { tag: t.emphasis, color: "rgb(var(--foreground))", fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: t.link, color: "rgb(var(--accent))", textDecoration: "underline" },
  { tag: t.url, color: "rgb(var(--accent))" },
  { tag: t.quote, color: "rgb(var(--foreground-muted))", fontStyle: "italic" },
  { tag: t.list, color: "rgb(var(--accent))" },
  { tag: t.monospace, color: "#79c0ff" }, // code spans + fenced
  // ── Python (and shared tokens) ───────────────────────────────
  { tag: t.keyword, color: "#ff7b72", fontWeight: "600" }, // def, class, if, return, ...
  { tag: t.controlKeyword, color: "#ff7b72", fontWeight: "600" },
  { tag: t.moduleKeyword, color: "#ff7b72", fontWeight: "600" },
  { tag: t.operatorKeyword, color: "#ff7b72" },
  { tag: t.definitionKeyword, color: "#ff7b72", fontWeight: "600" },
  { tag: [t.atom, t.bool, t.null], color: "#79c0ff" }, // True, False, None
  { tag: t.number, color: "#79c0ff" },
  { tag: t.string, color: "#a5d6ff" },
  { tag: t.regexp, color: "#a5d6ff" },
  { tag: t.comment, color: "#8b949e", fontStyle: "italic" },
  { tag: t.docComment, color: "#8b949e", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.definition(t.variableName))], color: "#d2a8ff" },
  { tag: t.className, color: "#ffa657" },
  { tag: t.definition(t.variableName), color: "rgb(var(--foreground))" },
  { tag: t.propertyName, color: "#79c0ff" },
  { tag: t.variableName, color: "rgb(var(--foreground))" },
  { tag: t.typeName, color: "#ffa657" },
  { tag: t.tagName, color: "#7ee787" },
  { tag: t.attributeName, color: "#79c0ff" },
  { tag: t.operator, color: "#ff7b72" },
  { tag: t.punctuation, color: "rgb(var(--foreground-muted))" },
  { tag: t.bracket, color: "rgb(var(--foreground-muted))" },
  { tag: t.meta, color: "rgb(var(--foreground-muted))" },
  { tag: t.invalid, color: "#ff7b72", textDecoration: "underline wavy" },
]);

/**
 * Markdown "live preview" — hide the raw markup characters (`#`, `*`,
 * `**`, `-`, backticks, brackets) on every line EXCEPT the one(s) the
 * cursor/selection touches, so the user gets a clean rendered look while
 * reading and full edit affordance the moment they click on a line.
 *
 * Pattern borrowed from Obsidian / Typora / Notion. CodeMirror 6 doesn't
 * ship this; we build it via a `ViewPlugin` that walks the lezer
 * syntax tree and emits zero-width `Decoration.replace` ranges over the
 * mark nodes.
 */
const HIDDEN_MARK_NODES = new Set<string>([
  "HeaderMark",
  "EmphasisMark", // single `*` / `_` for italic AND doubled for strong
  "CodeMark", // backticks around inline code + fence delimiters
  "LinkMark", // `[` `]` `(` `)`
  "QuoteMark", // leading `>` on blockquotes
  "ListMark", // `-` `*` `+` `1.` (kept hidden — indentation conveys structure)
  "HTMLTag", // raw HTML tags in markdown (rare)
  "TableDelimiter", // `|` cell separators + the `---|---` header rule (GFM)
  "StrikethroughMark", // `~~` GFM strikethrough
  "TaskMarker", // `[ ]` / `[x]` GFM task list checkbox
]);

function buildConcealments(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const protectedLines = new Set<number>();
  for (const range of view.state.selection.ranges) {
    protectedLines.add(view.state.doc.lineAt(range.from).number);
    if (range.empty) continue;
    protectedLines.add(view.state.doc.lineAt(range.to).number);
  }

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(view.state).iterate({
      from,
      to,
      enter(node) {
        if (!HIDDEN_MARK_NODES.has(node.name)) return;
        if (node.from === node.to) return;
        const line = view.state.doc.lineAt(node.from).number;
        if (protectedLines.has(line)) return;
        builder.add(node.from, node.to, Decoration.replace({}));
      },
    });
  }
  return builder.finish();
}

const markdownConcealer = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildConcealments(view);
    }
    update(u: ViewUpdate): void {
      if (u.docChanged || u.viewportChanged || u.selectionSet) {
        this.decorations = buildConcealments(u.view);
      }
    }
  },
  { decorations: (v) => v.decorations },
);

function buildTheme(): ReturnType<typeof EditorView.theme> {
  return EditorView.theme(
    {
      "&": {
        height: "100%",
        backgroundColor: "rgb(var(--background))",
        color: "rgb(var(--foreground))",
        fontSize: "13px",
        fontFamily: "var(--font-mono)",
      },
      ".cm-content": {
        caretColor: "rgb(var(--accent))",
      },
      ".cm-gutters": {
        backgroundColor: "rgb(var(--background-elevated))",
        color: "rgb(var(--foreground-muted))",
        borderRight: "1px solid rgb(var(--border))",
      },
      ".cm-activeLine": {
        backgroundColor: "rgb(var(--background-elevated) / 0.6)",
      },
      ".cm-activeLineGutter": {
        backgroundColor: "rgb(var(--background-elevated))",
        color: "rgb(var(--foreground))",
      },
      "&.cm-focused": {
        outline: "none",
      },
      ".cm-selectionBackground, ::selection": {
        backgroundColor: "rgb(var(--accent) / 0.3)",
      },
      ".cm-cursor": {
        borderLeftColor: "rgb(var(--accent))",
      },
    },
    { dark: true },
  );
}

export function CodeMirrorEditor(props: CodeMirrorEditorProps): React.JSX.Element {
  const {
    value,
    onChange,
    language = "python",
    height = "100%",
    readOnly = false,
    className,
    "aria-label": ariaLabel,
    "data-testid": testId,
  } = props;

  // Memoise so changing `value` doesn't rebuild the heavy extension array
  // on every keystroke. Recompute only when `language` flips.
  const extensions = useMemo(() => {
    const base = [
      // `markdownLanguage` enables the GFM extensions (tables, task lists,
      // strikethrough, autolinks). Without it the parser is commonmark-only
      // and `TableDelimiter` nodes never get emitted.
      language === "markdown"
        ? markdown({ base: markdownLanguage })
        : python(),
      buildTheme(),
      syntaxHighlighting(highlightStyle),
    ];
    return language === "markdown"
      ? [...base, markdownConcealer]
      : base;
  }, [language]);

  return (
    <div
      className={className}
      style={{ height, width: "100%" }}
      aria-label={ariaLabel}
      data-testid={testId}
    >
      <CodeMirror
        value={value}
        onChange={(next: string) => onChange(next)}
        extensions={extensions}
        readOnly={readOnly}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLine: true,
          highlightActiveLineGutter: true,
          foldGutter: true,
          autocompletion: true,
          bracketMatching: true,
          closeBrackets: true,
          indentOnInput: true,
          tabSize: 4,
        }}
        height="100%"
        theme="none"
      />
    </div>
  );
}

export default CodeMirrorEditor;
