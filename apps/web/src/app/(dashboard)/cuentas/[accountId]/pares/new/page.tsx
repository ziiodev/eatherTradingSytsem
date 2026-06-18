"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { type PairCreateInput } from "@/lib/pairs";
import { createAccountPair } from "@/lib/accounts";
import { PairForm } from "@/components/projects/PairForm";

/**
 * `/cuentas/[accountId]/pares/new` — create a pair under a fixed account.
 *
 * The owning account is taken from the route; the form locks the account
 * selector and submits through the nested create endpoint
 * (`POST /api/accounts/{accountId}/pairs`, which derives account_id from
 * the path and ignores any account_id in the body).
 */
export default function NewPairPage({
  params,
}: {
  params: Promise<{ accountId: string }>;
}): React.JSX.Element {
  const { accountId } = use(params);
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(values: PairCreateInput): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createAccountPair(accountId, values);
      toast.success("Par creado");
      router.push(`/cuentas/${accountId}/pares/${created.id}`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setError("Ya existe un par con ese nombre.");
        } else if (err.status === 400 || err.status === 422) {
          setError(
            "Validación fallida en el servidor. Revisa los campos del formulario.",
          );
        } else if (err.status === 404) {
          setError("La cuenta no existe o no te pertenece.");
        } else if (err.status === 401) {
          setError("Sesión expirada. Vuelve a iniciar sesión.");
        } else {
          setError(`Error inesperado (${err.status})`);
        }
      } else {
        setError("Error de red. Intenta de nuevo.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Nuevo par</h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Define el par. Cuando lo crees podrás activarlo desde su detalle.
        </p>
      </header>

      <PairForm
        mode="create"
        accountId={accountId}
        submitting={submitting}
        error={error}
        onSubmit={handleSubmit}
        onCancel={() => router.push(`/cuentas/${accountId}/pares`)}
      />
    </section>
  );
}
