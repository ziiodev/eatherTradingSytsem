/**
 * A muted, dashed, NON-draggable row shown for placeholder palette groups
 * (e.g. AI, Strategy Boost). It carries no drag payload and no onDragStart, so
 * it can never be dropped onto the canvas.
 */
export function PalettePlaceholderRow() {
  return (
    <div className="border-border text-muted-foreground w-full cursor-default rounded border border-dashed px-2 py-1 text-left text-sm italic">
      Próximamente
    </div>
  );
}
