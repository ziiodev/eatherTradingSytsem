"use client";

/**
 * Create-then-redirect entry point for a brand-new Expert Advisor.
 *
 * The static `new` segment takes precedence over the dynamic `[id]` segment.
 * On mount we POST a default EA and replace the URL with the real editor route
 * so `new` never stays in history.
 */
import { useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createEa, eaErrorMessage } from "../../_lib/eas";

export default function NewEditorPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // Guard against double-invocation (React Strict Mode mounts twice in dev).
  const started = useRef(false);

  const mutation = useMutation({
    mutationFn: () => createEa({ name: "Nuevo Expert Advisor" }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["eas"] });
      router.replace(`/eas/editor/${created.id}`);
    },
  });

  // `mutate` is stable from React Query; the ref keeps us to a single create.
  const mutate = mutation.mutate;
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    mutate();
  }, [mutate]);

  return (
    <main className="mx-auto max-w-md p-8 text-center">
      {mutation.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {eaErrorMessage(mutation.error)}
        </p>
      ) : (
        <p className="text-muted-foreground text-sm">
          Creando tu Expert Advisor…
        </p>
      )}
    </main>
  );
}
