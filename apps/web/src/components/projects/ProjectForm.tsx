"use client";

import * as React from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
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
import { listAgents, type AgentSummary, type AgentType } from "@/lib/agents";
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
    orchestrator_agent_id: detail.orchestrator_agent_id ?? undefined,
    investigator_agent_id: detail.investigator_agent_id ?? undefined,
    marker_agent_id: detail.marker_agent_id ?? undefined,
    worker_agent_id: detail.worker_agent_id ?? undefined,
    tutor_agent_id: detail.tutor_agent_id ?? undefined,
    auditor_agent_id: detail.auditor_agent_id ?? undefined,
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
  orchestrator_agent_id: undefined,
  investigator_agent_id: undefined,
  marker_agent_id: undefined,
  worker_agent_id: undefined,
  tutor_agent_id: undefined,
  auditor_agent_id: undefined,
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

  // Track current agent selections so we can warn the operator if the
  // same agent.id is assigned to more than one slot (charter: one
  // Orquestador / Investigador / Marker / Worker / Tutor / Auditor per
  // project — the backend doesn't enforce uniqueness across slots, so
  // we flag it client-side).
  const orchestratorAgentId =
    useWatch({ control, name: "orchestrator_agent_id" }) ?? "";
  const investigatorAgentId =
    useWatch({ control, name: "investigator_agent_id" }) ?? "";
  const markerAgentId =
    useWatch({ control, name: "marker_agent_id" }) ?? "";
  const workerAgentId =
    useWatch({ control, name: "worker_agent_id" }) ?? "";
  const tutorAgentId =
    useWatch({ control, name: "tutor_agent_id" }) ?? "";
  const auditorAgentId =
    useWatch({ control, name: "auditor_agent_id" }) ?? "";

  function toggleSession(session: (typeof TRADING_SESSIONS)[number]): void {
    const next = currentSessions.includes(session)
      ? currentSessions.filter((s) => s !== session)
      : [...currentSessions, session];
    setValue("trading_sessions", next, { shouldDirty: true });
  }

  const submit: SubmitHandler<ProjectCreateInput> = async (values) => {
    // Normalize empty strings → null so the backend clears the binding
    // instead of receiving the empty string (Pydantic will 422 on it).
    const normalized: ProjectCreateInput = {
      ...values,
      orchestrator_agent_id: values.orchestrator_agent_id || null,
      investigator_agent_id: values.investigator_agent_id || null,
      marker_agent_id: values.marker_agent_id || null,
      worker_agent_id: values.worker_agent_id || null,
      tutor_agent_id: values.tutor_agent_id || null,
      auditor_agent_id: values.auditor_agent_id || null,
    };
    await onSubmit(normalized);
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
          <TabsTrigger value="agentes">Agentes</TabsTrigger>
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
            {/* 5 columnas en ≥lg para que los 5 campos de identificación
                (Nombre / Símbolo / Marco temporal / MCP URL / MCP Puerto)
                quepan en una sola fila. Descripción y demás abajo a ancho
                completo. */}
            <CardContent className="grid grid-cols-1 gap-4 lg:grid-cols-5">
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

              {/* Descripción + Notas en la misma fila a 50/50 en ≥lg.
                  En pantallas más estrechas se apilan. Se envuelven en un
                  sub-grid dentro del col-span-5 del padre. */}
              <div className="grid gap-4 lg:col-span-5 lg:grid-cols-2">
                <Field
                  id="description"
                  label="Descripción"
                  error={errors.description?.message}
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
                >
                  <Textarea id="notes" rows={3} {...register("notes")} />
                </Field>
              </div>

              <fieldset className="flex flex-col gap-2 lg:col-span-5">
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

        {/* AGENTES — six pickers (Orquestador / Investigador / Marker /
            Worker / Tutor / Auditor). Each loads its own type-filtered
            list from /api/agents. Backend already filters by
            current_user.id so we never see other tenants' agents.

            Order convention (mirrors the charter prose "supervisor →
            research news → market signal → execute → sleep/teach →
            audit"): Orquestador → Investigador → Marker → Worker →
            Tutor → Auditor. Migration 0012 added the Marker and Tutor
            slots. */}
        <TabsContent value="agentes">
          <Card>
            <CardHeader>
              <CardTitle>Agentes</CardTitle>
              <CardDescription>
                Cada proyecto puede vincular un agente de cada tipo
                (Orquestador / Investigador / Marker / Worker / Tutor /
                Auditor).
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <AgentSlotPicker
                id="orchestrator_agent_id"
                type="orchestrator"
                label="Orquestador"
                description="Supervisor del proyecto — decide qué agente dispara."
                {...register("orchestrator_agent_id")}
              />
              <AgentSlotPicker
                id="investigator_agent_id"
                type="investigator"
                label="Investigador"
                description="Lee y resume todas las noticias relevantes."
                {...register("investigator_agent_id")}
              />
              <AgentSlotPicker
                id="marker_agent_id"
                type="marker"
                label="Marker"
                description="Da la señal del mercado y la opción a poner en marcha."
                {...register("marker_agent_id")}
              />
              <AgentSlotPicker
                id="worker_agent_id"
                type="worker"
                label="Worker"
                description="Ejecuta órdenes contra MT5 vía MCP."
                {...register("worker_agent_id")}
              />
              <AgentSlotPicker
                id="tutor_agent_id"
                type="tutor"
                label="Tutor"
                description="Conduce la Fase de Sueño y orquesta el aprendizaje."
                {...register("tutor_agent_id")}
              />
              <AgentSlotPicker
                id="auditor_agent_id"
                type="auditor"
                label="Auditor"
                description="Analiza la operativa, q-table y los informes de MT5."
                {...register("auditor_agent_id")}
              />
              {duplicateAgentWarning(
                orchestratorAgentId,
                investigatorAgentId,
                markerAgentId,
                workerAgentId,
                tutorAgentId,
                auditorAgentId,
              ) && (
                <p
                  role="alert"
                  className="rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] p-2 text-xs text-[rgb(var(--warning))]"
                >
                  {duplicateAgentWarning(
                    orchestratorAgentId,
                    investigatorAgentId,
                    markerAgentId,
                    workerAgentId,
                    tutorAgentId,
                    auditorAgentId,
                  )}
                </p>
              )}
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

// ---------------------------------------------------------------------------
// Agent slot picker — used by the "Agentes" tab. Loads the list of agents
// for a given type on mount and renders a native <Select> bound to
// react-hook-form via spread props (`name`, `onChange`, `ref`...).
//
// We accept `register("worker_agent_id")` returns and forward them to the
// inner <select>; that means the parent stays in control of validation
// and dirty tracking.
// ---------------------------------------------------------------------------
interface AgentSlotPickerProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  id: string;
  type: AgentType;
  label: string;
  description?: string;
}

const AgentSlotPicker = React.forwardRef<
  HTMLSelectElement,
  AgentSlotPickerProps
>(function AgentSlotPicker(
  { id, type, label, description, ...selectProps },
  ref,
) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoading(true);
      try {
        // Don't pre-filter by is_active — operators occasionally pick an
        // archived agent on purpose (e.g. roll back to a prior version).
        // We tag the inactive ones in the option label so the choice is
        // visible.
        const rows = await listAgents({ type });
        if (cancelled) return;
        setAgents(rows);
        setLoadError(null);
      } catch {
        if (cancelled) return;
        setLoadError("Error al cargar agentes");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [type]);

  const activeAgents = agents.filter((a) => a.is_active);
  const noneAvailable = !loading && !loadError && activeAgents.length === 0;

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {description && (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          {description}
        </p>
      )}
      {loading ? (
        <p
          className="text-xs text-[rgb(var(--foreground-muted))]"
          data-testid={`${id}-loading`}
        >
          Cargando…
        </p>
      ) : loadError ? (
        <p
          role="alert"
          className="text-xs text-[rgb(var(--danger))]"
        >
          {loadError}
        </p>
      ) : (
        <Select
          id={id}
          ref={ref}
          data-testid={`${id}-select`}
          disabled={noneAvailable}
          {...selectProps}
        >
          <option value="">(sin asignar)</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name} v{agent.version}
              {!agent.is_active ? " · Archivado" : ""}
            </option>
          ))}
        </Select>
      )}
      {noneAvailable && (
        <p className="text-xs text-[rgb(var(--foreground-muted))]">
          No tienes agentes {label.toLowerCase()} activos.{" "}
          <Link
            href="/agentes"
            className="text-[rgb(var(--accent))] underline-offset-2 hover:underline"
          >
            Crea uno en Agentes
          </Link>
          .
        </p>
      )}
    </div>
  );
});

/**
 * Returns a human warning string if the same agent.id has been bound to
 * two distinct slots (Orquestador / Investigador / Marker / Worker /
 * Tutor / Auditor). Returns null otherwise. The backend doesn't
 * enforce this — we only nudge the UI.
 */
function duplicateAgentWarning(
  orchestratorId: string,
  investigatorId: string,
  markerId: string,
  workerId: string,
  tutorId: string,
  auditorId: string,
): string | null {
  const labels: Record<string, string[]> = {};
  const push = (id: string, label: string): void => {
    if (!id) return;
    labels[id] = labels[id] ?? [];
    labels[id].push(label);
  };
  push(orchestratorId, "Orquestador");
  push(investigatorId, "Investigador");
  push(markerId, "Marker");
  push(workerId, "Worker");
  push(tutorId, "Tutor");
  push(auditorId, "Auditor");
  for (const slots of Object.values(labels)) {
    if (slots.length > 1) {
      return `El mismo agente está asignado como ${slots.join(" y ")}. Revisa la asignación.`;
    }
  }
  return null;
}

