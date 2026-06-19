"use client";

/**
 * Floating canvas control that re-centers and zooms the viewport to fit ALL
 * nodes. Uses React Flow's `fitView`, which automatically zooms out (minimize)
 * or in (maximize) as needed so every node is visible and centered.
 *
 * Mounted INSIDE <ReactFlow> (as a <Panel>) so it shares the useReactFlow
 * instance and renders over the canvas.
 */
import { useCallback } from "react";
import { Panel, useReactFlow } from "@xyflow/react";
import { Maximize2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export function CenterViewButton() {
  const { fitView } = useReactFlow();

  const onCenter = useCallback(() => {
    // padding leaves breathing room around the bounds; duration animates the
    // transition so the recenter reads clearly instead of snapping.
    void fitView({ padding: 0.2, duration: 300 });
  }, [fitView]);

  return (
    <Panel position="top-right">
      <Button
        size="sm"
        variant="outline"
        onClick={onCenter}
        title="Centrar y ajustar la vista a todos los nodos"
      >
        <Maximize2 className="h-4 w-4" />
        Centrar
      </Button>
    </Panel>
  );
}
