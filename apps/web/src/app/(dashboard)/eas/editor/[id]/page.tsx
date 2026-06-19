import { EditorToolbar } from "../../_flow/EditorToolbar";
import { EditorShell } from "../../_flow/EditorShell";

/**
 * Visual Expert Advisor editor for a single EA id.
 *
 * Server Component shell that composes the toolbar plus the EditorShell client
 * wrapper. EditorShell owns the <ReactFlowProvider> so the sidebar and canvas
 * share one React Flow instance. In Next 16 the dynamic route `params` is a
 * Promise and must be awaited.
 */
export default async function EditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // The editor is a full-bleed canvas tool: cancel the dashboard layout's
  // `<main className="p-6">` padding with a matching `-m-6` so the toolbar sits
  // flush against the navbar and side edges. `h-screen` keeps the canvas filling
  // the viewport so it never collapses.
  return (
    <div className="-m-6 flex h-screen flex-col">
      <EditorToolbar eaId={id} />
      <EditorShell eaId={id} />
    </div>
  );
}
