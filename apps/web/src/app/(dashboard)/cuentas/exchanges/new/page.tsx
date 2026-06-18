"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm, type SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  createExchange,
  exchangeCreateSchema,
  EXCHANGE_KINDS,
  EXCHANGE_KIND_LABEL,
  type ExchangeCreateInput,
} from "@/lib/exchanges";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * `/cuentas/exchanges/new` — minimal Exchange create surface.
 *
 * Per design, exchanges are managed inline (no full CRUD dashboard for v1).
 * This page lets the operator create an exchange so the Account form's
 * exchange selector has something to point at.
 */
export default function NewExchangePage(): React.JSX.Element {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<ExchangeCreateInput>({
    resolver: zodResolver(exchangeCreateSchema),
    defaultValues: { name: "", code: "", kind: "broker" },
    mode: "onBlur",
  });

  const submit: SubmitHandler<ExchangeCreateInput> = async (values) => {
    setSubmitting(true);
    setError(null);
    try {
      await createExchange(values);
      toast.success("Exchange creado");
      router.push("/cuentas");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Ya existe un exchange con ese código.");
      } else if (
        err instanceof ApiError &&
        (err.status === 400 || err.status === 422)
      ) {
        setError("Validación fallida. Revisa los campos.");
      } else {
        setError("Error inesperado al crear el exchange.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          Nuevo exchange
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Define el venue de trading (bróker / exchange / prop / demo).
        </p>
      </header>

      <form
        onSubmit={handleSubmit(submit)}
        className="flex flex-col gap-4"
        noValidate
      >
        <Card>
          <CardHeader>
            <CardTitle>Datos del exchange</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="name">
                Nombre<span className="ml-0.5 text-[rgb(var(--danger))]">*</span>
              </Label>
              <Input id="name" autoComplete="off" {...register("name")} />
              {errors.name?.message && (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {errors.name.message}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="code">
                Código<span className="ml-0.5 text-[rgb(var(--danger))]">*</span>
              </Label>
              <Input id="code" autoComplete="off" {...register("code")} />
              {errors.code?.message && (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {errors.code.message}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="kind">Tipo</Label>
              <Select id="kind" {...register("kind")}>
                {EXCHANGE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {EXCHANGE_KIND_LABEL[k]}
                  </option>
                ))}
              </Select>
            </div>
          </CardContent>
        </Card>

        {error && (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.push("/cuentas")}
            disabled={submitting}
          >
            Cancelar
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Creando…" : "Crear exchange"}
          </Button>
        </div>
      </form>
    </section>
  );
}
