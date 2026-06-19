"use client";

/**
 * Toolbar "Group" button.
 *
 * Reads the current canvas selection from the ephemeral groupUiStore and is
 * ENABLED only when >= 2 nodes are selected (the minimum a group needs). On click
 * it opens a proper modal (prefilled "Grupo N") instead of window.prompt; on
 * submit it creates the group via the undoable graphStore.createGroup (which
 * lands the group COLLAPSED) and clears the selection.
 *
 * createGroup enforces the real invariants (>=2 distinct members, non-empty
 * name); we only gate the button so the affordance reads as disabled until valid.
 */
import { useState } from "react";
import { Group } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useGraphStore } from "../_stores/graphStore";
import { useGroupUiStore } from "../_stores/groupUiStore";
import { GroupNameModal } from "./GroupNameModal";

export function GroupButton() {
  const selectedCount = useGroupUiStore((s) => s.selectedNodeIds.size);
  const createGroup = useGraphStore((s) => s.createGroup);
  const groupCount = useGraphStore((s) => s.groups.length);
  const [modalOpen, setModalOpen] = useState(false);

  const disabled = selectedCount < 2;

  const handleSubmit = (name: string) => {
    const ids = Array.from(useGroupUiStore.getState().selectedNodeIds);
    if (ids.length < 2) return;
    const id = createGroup(ids, name);
    if (!id) return; // Rejected by the store (e.g. <2 distinct members).
    // createGroup already lands the group collapsed; just drop the selection.
    useGroupUiStore.getState().setSelectedNodeIds([]);
  };

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setModalOpen(true)}
        disabled={disabled}
        title={
          disabled
            ? "Selecciona 2 o más nodos para agrupar"
            : "Agrupar los nodos seleccionados"
        }
      >
        <Group className="h-4 w-4" />
        Agrupar
      </Button>

      <GroupNameModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        initialName={`Grupo ${groupCount + 1}`}
        title="Nuevo grupo"
        submitLabel="Crear"
        onSubmit={handleSubmit}
      />
    </>
  );
}
