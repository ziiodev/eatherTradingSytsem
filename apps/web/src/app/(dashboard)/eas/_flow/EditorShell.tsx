"use client";

/**
 * Client wrapper for the editor row. Establishes a single <ReactFlowProvider>
 * so useReactFlow (screenToFlowPosition) is shared between the sidebar and the
 * canvas. Param editing happens IN-PLACE on each node's expanded body, so there
 * is no longer a right-side inspector (nor any selection plumbing to feed it).
 *
 * page.tsx is a Server Component; this client shell is the documented boundary
 * for the React-Flow context provider.
 */
import { useEffect } from "react";
import { ReactFlowProvider } from "@xyflow/react";

import { NodeSidebar } from "./NodeSidebar";
import { FlowCanvas } from "./FlowCanvas";
import { StrategyHydrator } from "./StrategyHydrator";
import { useGraphStore } from "../_stores/graphStore";

/** True when keyboard focus is in an editable control we must not hijack. */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  );
}

/**
 * Global keyboard shortcuts for undo/redo. Bound at the editor shell so they
 * work anywhere on the canvas, but GUARDED so they don't fire while typing in an
 * input/textarea/select (or other editable element).
 */
function UndoRedoShortcuts() {
  const undo = useGraphStore((s) => s.undo);
  const redo = useGraphStore((s) => s.redo);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (isEditableTarget(e.target)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        // Ctrl+Shift+Z or Ctrl+Y → redo.
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo]);

  return null;
}

export function EditorShell({ eaId }: { eaId: string }) {
  return (
    <ReactFlowProvider>
      <div className="flex flex-1 overflow-hidden">
        <NodeSidebar />
        {/* `relative` anchors the hydrator's loading/404 overlay. */}
        <div className="relative flex-1">
          <StrategyHydrator id={eaId} />
          <FlowCanvas />
        </div>
      </div>
      <UndoRedoShortcuts />
    </ReactFlowProvider>
  );
}
