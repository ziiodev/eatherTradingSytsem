"use client";

import * as React from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useForm, useWatch, type SubmitHandler } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  pairCreateSchema,
  TIMEFRAMES,
  TRADING_SESSIONS,
  TRADING_SESSION_LABEL,
  type PairCreateInput,
  type PairDetail,
} from "@/lib/pairs";
import { listAccounts, type TradingAccount } from "@/lib/accounts";
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
 * Reusable pair (Par) form. Used by both:
 *   - /cuentas/[accountId]/pares/new   (create — POST /api/accounts/{id}/pairs)
 *   - /cuentas/[accountId]/pares/[pairId]/configuracion (edit — PATCH /api/pairs/{id})
 *
 * accounts-pairs-restructure: the broker-credential block (login/server/
 * broker/currency/leverage/type) was REMOVED from this form — it now lives on
 * the Account (Cuenta). The form gains a required ``account_id`` (rendered as
 * a read-only chip when the owning account is fixed by the route, or as a
 * selector otherwise).
 *
 * Tabs: General / Riesgo / Costes / Estrategia / Agentes. The old "Cuenta"
 * tab is gone.
 */

export type PairFormMode = "create" | "edit";

export interface PairFormProps {
  mode: PairFormMode;
  initial?: PairDetail;
  /**
   * The owning account. On the create route this is taken from the URL; on
   * edit it comes from ``initial.account_id``. When provided, the account
   * selector is locked.
   */
  accountId?: string;
  submitting: boolean;
  error?: string | null;
  onSubmit: (values: PairCreateInput) => Promise<void> | void;
  onCancel?: () => void;
}

function detailToFormValues(detail: PairDetail): PairCreateInput {
  return {
    account_id: detail.account_id,
    name: detail.name,
    description: detail.description ?? undefined,
    symbol: detail.symbol,
    timeframe: detail.timeframe as PairCreateInput["timeframe"],
    mcp_url: detail.mcp_url,
    mcp_port: detail.mcp_port ?? undefined,
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

function defaultValues(accountId?: string): PairCreateInput {
  return {
    account_id: accountId ?? "",
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
}

export function PairForm({
  mode,
  initial,
  accountId,
  submitting,
  error,
  onSubmit,
  onCancel,
}: PairFormProps): React.JSX.Element {
  const [activeTab, setActiveTab] = useState("general");
  const lockedAccountId = accountId ?? initial?.account_id;

  const {
    register,
    handleSubmit,
    setValue,
    control,
    formState: { errors },
  } = useForm<PairCreateInput>({
    resolver: zodResolver(pairCreateSchema),
    defaultValues: initial
      ? detailToFormValues(initial)
      : defaultValues(accountId),
    mode: "onBlur",
  });

  // ``useWatch`` is the React-19-compiler-safe alternative to ``watch()``.
  const currentSessions =
    useWatch({ control, name: "trading_sessions" }) ?? [];

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

  const submit: SubmitHandler<PairCreateInput> = async (values) => {
    const normalized: PairCreateInput = {
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
                Identificación del par y configuración MCP. El par pertenece a
                una cuenta, que aporta las credenciales del broker.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-4 lg:grid-cols-5">
              {/* Account selector — locked when the owning account is fixed
                  by the route (create-under-account or edit). */}
              <AccountSlotPicker
                id="account_id"
                lockedAccountId={lockedAccountId}
                error={errors.account_id?.message}
                {...register("account_id")}
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
                Descripción y lógica base del par.
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
            list from /api/agents. */}
        <TabsContent value="agentes">
          <Card>
            <CardHeader>
              <CardTitle>Agentes</CardTitle>
              <CardDescription>
                Cada par puede vincular un agente de cada tipo
                (Orquestador / Investigador / Marker / Worker / Tutor /
                Auditor).
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <AgentSlotPicker
                id="orchestrator_agent_id"
                type="orchestrator"
                label="Orquestador"
                description="Supervisor del par — decide qué agente dispara."
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
              ? "Crear par"
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
// Account slot picker — binds the required ``account_id``. When the owning
// account is fixed by the route (``lockedAccountId``) the control renders the
// account name as a read-only chip plus a hidden input that still forwards the
// react-hook-form registration so the value is submitted.
// ---------------------------------------------------------------------------
interface AccountSlotPickerProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  id: string;
  lockedAccountId?: string;
  error?: string;
}

const AccountSlotPicker = React.forwardRef<
  HTMLSelectElement,
  AccountSlotPickerProps
>(function AccountSlotPicker(
  { id, lockedAccountId, error, ...selectProps },
  ref,
) {
  const [accounts, setAccounts] = useState<TradingAccount[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      setLoading(true);
      try {
        const res = await listAccounts({ limit: 100 });
        if (cancelled) return;
        setAccounts(res.items);
      } catch {
        // selector falls back to the locked chip / empty list
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const lockedAccount = accounts.find((a) => a.id === lockedAccountId);

  if (lockedAccountId) {
    return (
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={id}>Cuenta</Label>
        <div className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] px-3 py-2 text-sm">
          {lockedAccount ? lockedAccount.name : "Cuenta vinculada"}
        </div>
        {/* Hidden control still carries the registered value. */}
        <Select
          id={id}
          ref={ref}
          className="hidden"
          aria-hidden
          {...selectProps}
        >
          <option value={lockedAccountId}>
            {lockedAccount?.name ?? lockedAccountId}
          </option>
        </Select>
        {error && <p className="text-xs text-[rgb(var(--danger))]">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>
        Cuenta<span className="ml-0.5 text-[rgb(var(--danger))]">*</span>
      </Label>
      <Select id={id} ref={ref} disabled={loading} {...selectProps}>
        <option value="">(selecciona una cuenta)</option>
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.name}
          </option>
        ))}
      </Select>
      {error && <p className="text-xs text-[rgb(var(--danger))]">{error}</p>}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Agent slot picker — used by the "Agentes" tab.
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
 * two distinct slots. Returns null otherwise.
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
