"use client";

import * as React from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useForm, useWatch, type SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  accountCreateSchema,
  ACCOUNT_TYPES,
  ACCOUNT_TYPE_LABEL,
  type TradingAccount,
  type TradingAccountCreateInput,
} from "@/lib/accounts";
import { listExchanges, type Exchange } from "@/lib/exchanges";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

/**
 * Reusable Account (Cuenta) form. Used by both:
 *   - /cuentas/new          (create — POST /api/accounts)
 *   - /cuentas/[id]/...     (edit — PATCH /api/accounts/{id})
 *
 * accounts-pairs-restructure: the broker-credential block lives HERE (it was
 * lifted off the old project/pair). The form also picks the owning Exchange.
 *
 * MFA gate: the backend rejects creating/patching an account with
 * ``account_type='real'`` when the operator has no MFA enabled (409
 * MFA_REQUIRED_FOR_REAL_ACCOUNT). The form surfaces a warning when "real" is
 * selected so the operator isn't surprised by the server-side rejection.
 */

export type AccountFormMode = "create" | "edit";

export interface AccountFormProps {
  mode: AccountFormMode;
  initial?: TradingAccount;
  submitting: boolean;
  error?: string | null;
  onSubmit: (values: TradingAccountCreateInput) => Promise<void> | void;
  onCancel?: () => void;
}

function detailToFormValues(detail: TradingAccount): TradingAccountCreateInput {
  return {
    exchange_id: detail.exchange_id,
    name: detail.name,
    description: detail.description ?? undefined,
    account_login: detail.account_login ?? undefined,
    account_server: detail.account_server ?? undefined,
    broker_name: detail.broker_name ?? undefined,
    account_credential_ref: detail.account_credential_ref ?? undefined,
    account_currency: detail.account_currency ?? undefined,
    account_leverage: detail.account_leverage ?? undefined,
    account_type: detail.account_type ?? undefined,
  };
}

const DEFAULT_VALUES: TradingAccountCreateInput = {
  exchange_id: "",
  name: "",
  description: "",
  account_login: "",
  account_server: "",
  broker_name: "",
  account_credential_ref: "",
  account_currency: "USD",
  account_leverage: undefined,
  account_type: "demo",
};

export function AccountForm({
  mode,
  initial,
  submitting,
  error,
  onSubmit,
  onCancel,
}: AccountFormProps): React.JSX.Element {
  const [activeTab, setActiveTab] = useState("general");

  const {
    register,
    handleSubmit,
    control,
    formState: { errors },
  } = useForm<TradingAccountCreateInput>({
    resolver: zodResolver(accountCreateSchema),
    defaultValues: initial ? detailToFormValues(initial) : DEFAULT_VALUES,
    mode: "onBlur",
  });

  const accountType = useWatch({ control, name: "account_type" }) ?? "";

  const submit: SubmitHandler<TradingAccountCreateInput> = async (values) => {
    await onSubmit(values);
  };

  return (
    <form
      onSubmit={handleSubmit(submit)}
      className="flex flex-col gap-4"
      noValidate
    >
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="credenciales">Credenciales</TabsTrigger>
        </TabsList>

        {/* GENERAL */}
        <TabsContent value="general">
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
              <CardDescription>
                Identificación de la cuenta y exchange al que pertenece.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <ExchangeSlotPicker
                id="exchange_id"
                error={errors.exchange_id?.message}
                {...register("exchange_id")}
              />
              <Field
                id="name"
                label="Nombre"
                required
                error={errors.name?.message}
              >
                <Input id="name" autoComplete="off" {...register("name")} />
              </Field>
              <Field
                id="account_type"
                label="Tipo de cuenta"
                error={errors.account_type?.message}
                className="md:col-span-2"
              >
                <Select id="account_type" {...register("account_type")}>
                  {ACCOUNT_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {ACCOUNT_TYPE_LABEL[t]}
                    </option>
                  ))}
                </Select>
                {accountType === "real" && (
                  <p className="text-xs text-[rgb(var(--warning))]">
                    Las cuentas reales requieren MFA activado. Si no lo tienes,
                    el servidor rechazará la operación. Actívalo en{" "}
                    <Link
                      href="/configuracion"
                      className="text-[rgb(var(--accent))] underline-offset-2 hover:underline"
                    >
                      Configuración
                    </Link>
                    .
                  </p>
                )}
              </Field>
              <Field
                id="description"
                label="Descripción"
                error={errors.description?.message}
                className="md:col-span-2"
              >
                <Textarea
                  id="description"
                  rows={3}
                  {...register("description")}
                />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>

        {/* CREDENCIALES */}
        <TabsContent value="credenciales">
          <Card>
            <CardHeader>
              <CardTitle>Credenciales del broker</CardTitle>
              <CardDescription>
                Datos del broker. La contraseña/secreto se guarda en un secreto
                externo referenciado por <code>account_credential_ref</code>,
                nunca en texto plano.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Field
                id="account_login"
                label="Login"
                error={errors.account_login?.message}
              >
                <Input
                  id="account_login"
                  autoComplete="off"
                  {...register("account_login")}
                />
              </Field>
              <Field
                id="account_server"
                label="Servidor"
                error={errors.account_server?.message}
              >
                <Input id="account_server" {...register("account_server")} />
              </Field>
              <Field
                id="broker_name"
                label="Broker"
                error={errors.broker_name?.message}
              >
                <Input id="broker_name" {...register("broker_name")} />
              </Field>
              <Field
                id="account_credential_ref"
                label="Referencia de credencial (secreto)"
                error={errors.account_credential_ref?.message}
              >
                <Input
                  id="account_credential_ref"
                  autoComplete="off"
                  {...register("account_credential_ref")}
                />
              </Field>
              <Field
                id="account_currency"
                label="Divisa"
                error={errors.account_currency?.message}
              >
                <Input
                  id="account_currency"
                  {...register("account_currency")}
                />
              </Field>
              <Field
                id="account_leverage"
                label="Apalancamiento"
                error={errors.account_leverage?.message}
              >
                <Input
                  id="account_leverage"
                  type="number"
                  {...register("account_leverage")}
                />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {error && (
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={submitting}
          >
            Cancelar
          </Button>
        )}
        <Button type="submit" disabled={submitting}>
          {submitting
            ? mode === "create"
              ? "Creando…"
              : "Guardando…"
            : mode === "create"
              ? "Crear cuenta"
              : "Guardar cambios"}
        </Button>
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  error,
  required,
  className,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className={`flex flex-col gap-1.5 ${className ?? ""}`}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="ml-0.5 text-[rgb(var(--danger))]">*</span>}
      </Label>
      {children}
      {error && <p className="text-xs text-[rgb(var(--danger))]">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Exchange selector — inline per design (no dedicated exchanges management
// surface for v1; the selector loads the operator's exchanges and offers a
// link to create one).
// ---------------------------------------------------------------------------
interface ExchangeSlotPickerProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  id: string;
  error?: string;
}

const ExchangeSlotPicker = React.forwardRef<
  HTMLSelectElement,
  ExchangeSlotPickerProps
>(function ExchangeSlotPicker({ id, error, ...selectProps }, ref) {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoading(true);
      try {
        const res = await listExchanges({ limit: 100 });
        if (cancelled) return;
        setExchanges(res.items);
      } catch {
        // empty list — operator can still see the create link
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const noneAvailable = !loading && exchanges.length === 0;

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>
        Exchange<span className="ml-0.5 text-[rgb(var(--danger))]">*</span>
      </Label>
      <Select id={id} ref={ref} disabled={loading} {...selectProps}>
        <option value="">(selecciona un exchange)</option>
        {exchanges.map((ex) => (
          <option key={ex.id} value={ex.id}>
            {ex.name} ({ex.code})
          </option>
        ))}
      </Select>
      {noneAvailable && (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          No tienes exchanges aún.{" "}
          <Link
            href="/cuentas/exchanges/new"
            className="text-[rgb(var(--accent))] underline-offset-2 hover:underline"
          >
            Crea uno
          </Link>
          .
        </p>
      )}
      {error && <p className="text-xs text-[rgb(var(--danger))]">{error}</p>}
    </div>
  );
});
