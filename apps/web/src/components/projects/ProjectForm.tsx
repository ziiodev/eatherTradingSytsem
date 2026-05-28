"use client";

import { useState } from "react";
import { useForm, useWatch, type SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  projectCreateSchema,
  TIMEFRAMES,
  TRADING_SESSIONS,
  TRADING_SESSION_LABEL,
  type ProjectCreateInput,
  type ProjectDetail,
} from "@/lib/projects";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
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
 * Reusable project form. Used by both:
 *   - /proyectos/new   (create — submits POST /api/projects)
 *   - /proyectos/[id]  (edit — submits PATCH /api/projects/{id})
 *
 * Tabs match the design doc: General / Riesgo / Cuenta / Costes / Estrategia.
 * Lifecycle controls live OUTSIDE this form in the detail header.
 */

export type ProjectFormMode = "create" | "edit";

export interface ProjectFormProps {
  mode: ProjectFormMode;
  initial?: ProjectDetail;
  submitting: boolean;
  error?: string | null;
  onSubmit: (values: ProjectCreateInput) => Promise<void> | void;
  onCancel?: () => void;
}

function detailToFormValues(detail: ProjectDetail): ProjectCreateInput {
  return {
    name: detail.name,
    description: detail.description ?? undefined,
    symbol: detail.symbol,
    timeframe: detail.timeframe as ProjectCreateInput["timeframe"],
    mcp_url: detail.mcp_url,
    mcp_port: detail.mcp_port ?? undefined,
    account_login: detail.account_login ?? undefined,
    account_server: detail.account_server ?? undefined,
    broker_name: detail.broker_name ?? undefined,
    account_currency: detail.account_currency ?? undefined,
    account_leverage: detail.account_leverage ?? undefined,
    account_type: detail.account_type ?? undefined,
    capital_asignado: detail.capital_asignado ?? undefined,
    commission_per_lot: detail.commission_per_lot ?? undefined,
    commission_currency: detail.commission_currency ?? undefined,
    risk_per_trade: detail.risk_per_trade ?? undefined,
    max_daily_dd: detail.max_daily_dd ?? undefined,
    max_total_dd: detail.max_total_dd ?? undefined,
    max_exposure: detail.max_exposure ?? undefined,
    strategy_description: detail.strategy_description ?? undefined,
    base_logic: detail.base_logic ?? undefined,
    trading_sessions: detail.trading_sessions,
    tags: detail.tags ?? undefined,
    notes: detail.notes ?? undefined,
  };
}

const DEFAULT_VALUES: ProjectCreateInput = {
  name: "",
  description: "",
  symbol: "EURUSD",
  timeframe: "H1",
  mcp_url: "http://localhost:8081",
  mcp_port: undefined,
  trading_sessions: [],
  risk_per_trade: "1.0",
  max_daily_dd: "3.0",
  max_total_dd: "8.0",
  max_exposure: "10.0",
};

export function ProjectForm({
  mode,
  initial,
  submitting,
  error,
  onSubmit,
  onCancel,
}: ProjectFormProps): React.JSX.Element {
  const [activeTab, setActiveTab] = useState("general");

  const {
    register,
    handleSubmit,
    setValue,
    control,
    formState: { errors },
  } = useForm<ProjectCreateInput>({
    resolver: zodResolver(projectCreateSchema),
    defaultValues: initial ? detailToFormValues(initial) : DEFAULT_VALUES,
    mode: "onBlur",
  });

  // ``useWatch`` is the React-19-compiler-safe alternative to ``watch()``
  // — it subscribes only this component to changes in ``trading_sessions``
  // without forcing the parent to re-render on every keystroke.
  const currentSessions =
    useWatch({ control, name: "trading_sessions" }) ?? [];

  function toggleSession(session: (typeof TRADING_SESSIONS)[number]): void {
    const next = currentSessions.includes(session)
      ? currentSessions.filter((s) => s !== session)
      : [...currentSessions, session];
    setValue("trading_sessions", next, { shouldDirty: true });
  }

  const submit: SubmitHandler<ProjectCreateInput> = async (values) => {
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
          <TabsTrigger value="riesgo">Riesgo</TabsTrigger>
          <TabsTrigger value="cuenta">Cuenta</TabsTrigger>
          <TabsTrigger value="costes">Costes</TabsTrigger>
          <TabsTrigger value="estrategia">Estrategia</TabsTrigger>
        </TabsList>

        {/* GENERAL */}
        <TabsContent value="general">
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
              <CardDescription>
                Identificación del proyecto y configuración MCP.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Field
                id="name"
                label="Nombre"
                required
                error={errors.name?.message}
              >
                <Input id="name" autoComplete="off" {...register("name")} />
              </Field>

              <Field
                id="symbol"
                label="Símbolo"
                required
                error={errors.symbol?.message}
              >
                <Input
                  id="symbol"
                  autoComplete="off"
                  {...register("symbol")}
                />
              </Field>

              <Field
                id="timeframe"
                label="Marco temporal"
                required
                error={errors.timeframe?.message}
              >
                <Select id="timeframe" {...register("timeframe")}>
                  {TIMEFRAMES.map((tf) => (
                    <option key={tf} value={tf}>
                      {tf}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                id="mcp_url"
                label="MCP URL"
                required
                error={errors.mcp_url?.message}
              >
                <Input id="mcp_url" {...register("mcp_url")} />
              </Field>

              <Field
                id="mcp_port"
                label="MCP Puerto"
                error={errors.mcp_port?.message}
              >
                <Input
                  id="mcp_port"
                  type="number"
                  min={1}
                  max={65535}
                  {...register("mcp_port")}
                />
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

              <Field
                id="notes"
                label="Notas"
                error={errors.notes?.message}
                className="md:col-span-2"
              >
                <Textarea id="notes" rows={3} {...register("notes")} />
              </Field>

              <fieldset className="md:col-span-2 flex flex-col gap-2">
                <legend className="text-sm font-medium text-[rgb(var(--foreground))]">
                  Sesiones de trading
                </legend>
                <p className="text-xs text-[rgb(var(--foreground-muted))]">
                  El worker solo operará dentro de las sesiones seleccionadas.
                </p>
                <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                  {TRADING_SESSIONS.map((session) => {
                    const checked = currentSessions.includes(session);
                    return (
                      <label
                        key={session}
                        className="flex items-center gap-2 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-2 py-1.5 text-sm"
                      >
                        <Checkbox
                          checked={checked}
                          onChange={() => toggleSession(session)}
                        />
                        <span>{TRADING_SESSION_LABEL[session]}</span>
                      </label>
                    );
                  })}
                </div>
                {errors.trading_sessions?.message && (
                  <p className="text-xs text-[rgb(var(--danger))]">
                    {errors.trading_sessions.message}
                  </p>
                )}
              </fieldset>
            </CardContent>
          </Card>
        </TabsContent>

        {/* RIESGO */}
        <TabsContent value="riesgo">
          <Card>
            <CardHeader>
              <CardTitle>Riesgo</CardTitle>
              <CardDescription>
                Topes por trade, diarios y totales. Por defecto: 1% / 3% / 8% /
                10% (CHARTER).
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Field
                id="risk_per_trade"
                label="Riesgo por trade (%)"
                error={errors.risk_per_trade?.message}
              >
                <Input
                  id="risk_per_trade"
                  type="number"
                  step="0.01"
                  {...register("risk_per_trade")}
                />
              </Field>
              <Field
                id="max_daily_dd"
                label="DD diario máx (%)"
                error={errors.max_daily_dd?.message}
              >
                <Input
                  id="max_daily_dd"
                  type="number"
                  step="0.01"
                  {...register("max_daily_dd")}
                />
              </Field>
              <Field
                id="max_total_dd"
                label="DD total máx (%)"
                error={errors.max_total_dd?.message}
              >
                <Input
                  id="max_total_dd"
                  type="number"
                  step="0.01"
                  {...register("max_total_dd")}
                />
              </Field>
              <Field
                id="max_exposure"
                label="Exposición máx (%)"
                error={errors.max_exposure?.message}
              >
                <Input
                  id="max_exposure"
                  type="number"
                  step="0.01"
                  {...register("max_exposure")}
                />
              </Field>
              <Field
                id="capital_asignado"
                label="Capital asignado"
                error={errors.capital_asignado?.message}
              >
                <Input
                  id="capital_asignado"
                  type="number"
                  step="0.01"
                  {...register("capital_asignado")}
                />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>

        {/* CUENTA */}
        <TabsContent value="cuenta">
          <Card>
            <CardHeader>
              <CardTitle>Cuenta de trading</CardTitle>
              <CardDescription>
                Datos del broker. Las credenciales se guardan en un secreto
                separado (campo ``account_credential_ref``).
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
              <Field
                id="account_type"
                label="Tipo de cuenta"
                error={errors.account_type?.message}
              >
                <Input id="account_type" {...register("account_type")} />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>

        {/* COSTES */}
        <TabsContent value="costes">
          <Card>
            <CardHeader>
              <CardTitle>Costes y comisiones</CardTitle>
              <CardDescription>
                Modelado de comisión, swap y spread típico.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Field
                id="commission_per_lot"
                label="Comisión por lote"
                error={errors.commission_per_lot?.message}
              >
                <Input
                  id="commission_per_lot"
                  type="number"
                  step="0.0001"
                  {...register("commission_per_lot")}
                />
              </Field>
              <Field
                id="commission_currency"
                label="Divisa de comisión"
                error={errors.commission_currency?.message}
              >
                <Input
                  id="commission_currency"
                  {...register("commission_currency")}
                />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ESTRATEGIA */}
        <TabsContent value="estrategia">
          <Card>
            <CardHeader>
              <CardTitle>Estrategia</CardTitle>
              <CardDescription>
                Descripción y lógica base del proyecto.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <Field
                id="strategy_description"
                label="Descripción de la estrategia"
                error={errors.strategy_description?.message}
              >
                <Textarea
                  id="strategy_description"
                  rows={4}
                  {...register("strategy_description")}
                />
              </Field>
              <Field
                id="base_logic"
                label="Lógica base"
                error={errors.base_logic?.message}
              >
                <Textarea
                  id="base_logic"
                  rows={10}
                  className="font-mono"
                  {...register("base_logic")}
                />
              </Field>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {error && (
        <p
          role="alert"
          className="text-sm text-[rgb(var(--danger))]"
        >
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
              ? "Crear proyecto"
              : "Guardar cambios"}
        </Button>
      </div>
    </form>
  );
}

// Helper for the typical "label + input + error" trio.
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
      {error && (
        <p className="text-xs text-[rgb(var(--danger))]">{error}</p>
      )}
    </div>
  );
}
