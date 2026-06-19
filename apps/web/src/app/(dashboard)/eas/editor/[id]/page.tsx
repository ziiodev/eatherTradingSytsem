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

  return (
    <div className="flex h-screen flex-col">
      <EditorToolbar eaId={id} />
      <EditorShell eaId={id} />
    </div>
  );
}
