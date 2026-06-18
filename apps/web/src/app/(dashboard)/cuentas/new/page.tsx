"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  createAccount,
  type TradingAccountCreateInput,
} from "@/lib/accounts";
import { AccountForm } from "@/components/accounts/AccountForm";

export default function NewAccountPage(): React.JSX.Element {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(
    values: TradingAccountCreateInput,
  ): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createAccount(values);
      toast.success("Cuenta creada");
      router.push(`/cuentas/${created.id}/pares`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (
          err.status === 409 &&
          typeof err.body === "object" &&
          err.body !== null &&
          (err.body as { detail?: { code?: string } }).detail?.code ===
            "MFA_REQUIRED_FOR_REAL_ACCOUNT"
        ) {
          setError(
            "Las cuentas reales requieren MFA. Actívalo en Configuración antes de crear una cuenta real.",
          );
        } else if (err.status === 409) {
          setError("Conflicto al crear la cuenta.");
        } else if (err.status === 400 || err.status === 422) {
          setError(
            "Validación fallida en el servidor. Revisa los campos del formulario.",
          );
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
        <h1 className="text-2xl font-semibold tracking-tight">Nueva cuenta</h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Define la cuenta del broker. Sus pares heredarán estas credenciales.
        </p>
      </header>

      <AccountForm
        mode="create"
        submitting={submitting}
        error={error}
        onSubmit={handleSubmit}
        onCancel={() => router.push("/cuentas")}
      />
    </section>
  );
}
