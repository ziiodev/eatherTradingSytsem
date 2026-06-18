"use client";

/**
 * Sleep Report viewer — renders the structured outcome of one sleep run.
 *
 * Sections:
 *  - Header  : sleep_type, started_at, finished_at, overall_score badge.
 *  - Auditor metrics (JSONB → key/value table).
 *  - Worker insights (list).
 *  - Improvements applied (list with risk badges).
 *  - Q-Table version before → after (links to the q-tables page).
 *
 * Data source: GET /api/pairs/{id}/sleep-runs/{runId}/report combined
 * with GET /api/pairs/{id}/sleep/runs/{runId} for the run metadata
 * (sleep_type, started_at, ended_at).
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import { ApiError } from "@/lib/api";
import {
  fetchSleepReport,
  getSleepRun,
  RISK_CLASS_LABEL,
  SLEEP_PHASE_LABEL,
  SLEEP_RUN_STATUS_LABEL,
  type ConfigVersionRiskClass,
  type SleepReport,
  type SleepRunDetailResponse,
} from "@/lib/sleep";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ImprovementEntry {
  title?: string;
  description?: string;
  risk?: ConfigVersionRiskClass | string;
  [k: string]: unknown;
}

interface WorkerInsightEntry {
  title?: string;
  body?: string;
  [k: string]: unknown;
}

export default function SleepRunReportPage({
  params,
}: {
  params: Promise<{ accountId: string; pairId: string; runId: string }>;
}): React.JSX.Element {
  const { accountId, pairId, runId } = use(params);

  const [run, setRun] = useState<SleepRunDetailResponse | null>(null);
  const [report, setReport] = useState<SleepReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const [runData, reportData] = await Promise.all([
          getSleepRun(pairId, runId),
          fetchSleepReport(pairId, runId),
        ]);
        if (cancelled) return;
        setRun(runData);
        setReport(reportData);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError(
            err instanceof ApiError
              ? `Error (${err.status})`
              : "Error de red",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [pairId, runId]);

  if (loading) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink accountId={accountId} pairId={pairId} />
        <p className="text-sm text-[rgb(var(--foreground-muted))]">Cargando…</p>
      </section>
    );
  }
  if (notFound) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink accountId={accountId} pairId={pairId} />
        <p className="text-sm">Informe no encontrado.</p>
      </section>
    );
  }
  if (error || !run || !report) {
    return (
      <section className="flex flex-col gap-4">
        <BackLink accountId={accountId} pairId={pairId} />
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error ?? "Error desconocido."}
        </p>
      </section>
    );
  }

  const payload = report.payload ?? {};
  const auditorMetrics = readObject(payload.auditor_metrics);
  const workerInsights = readArray<WorkerInsightEntry>(payload.worker_insights);
  const improvements = readArray<ImprovementEntry>(payload.improvements_applied);
  const overallScore = readNumber(payload.overall_score);
  const qBefore = readNumber(payload.q_table_before);
  const qAfter = readNumber(payload.q_table_after);

  return (
    <section
      className="flex flex-col gap-4"
      data-testid="sleep-report-section"
    >
      <BackLink accountId={accountId} pairId={pairId} />
      <Header
        pairId={pairId}
        runId={runId}
        run={run}
        overallScore={overallScore}
      />

      <AuditorMetricsCard metrics={auditorMetrics} />
      <WorkerInsightsCard insights={workerInsights} />
      <ImprovementsCard improvements={improvements} />
      <QTableVersionsCard
        accountId={accountId}
        pairId={pairId}
        before={qBefore}
        after={qAfter}
      />

      {report.summary_md ? (
        <Card data-testid="report-summary-md">
          <CardHeader>
            <CardTitle>Resumen markdown</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap text-xs text-[rgb(var(--foreground))]">
              {report.summary_md}
            </pre>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sub-cards
// ---------------------------------------------------------------------------
function Header({
  pairId,
  runId,
  run,
  overallScore,
}: {
  pairId: string;
  runId: string;
  run: SleepRunDetailResponse;
  overallScore: number | null;
}): React.JSX.Element {
  return (
    <header
      className="flex flex-wrap items-center gap-3"
      data-testid="report-header"
    >
      <h2 className="text-2xl font-semibold tracking-tight">
        Sleep Run · {SLEEP_PHASE_LABEL[run.run.phase_type]}
      </h2>
      <Badge variant={overallScoreVariant(overallScore)}>
        {overallScore !== null
          ? `score ${overallScore.toFixed(2)}`
          : "sin score"}
      </Badge>
      <Badge variant="muted">
        {SLEEP_RUN_STATUS_LABEL[run.run.status]}
      </Badge>
      <span className="text-xs text-[rgb(var(--foreground-muted))]">
        {run.run.started_at ?? "—"} → {run.run.ended_at ?? "—"}
      </span>
      <span className="ml-auto text-xs text-[rgb(var(--foreground-muted))]">
        run_id: <code>{runId}</code> · par: <code>{pairId}</code>
      </span>
    </header>
  );
}

function AuditorMetricsCard({
  metrics,
}: {
  metrics: Record<string, unknown>;
}): React.JSX.Element {
  const entries = Object.entries(metrics);
  return (
    <Card data-testid="report-auditor-metrics">
      <CardHeader>
        <CardTitle>Métricas del Auditor</CardTitle>
        <CardDescription>
          Valores agregados al cierre del run (net Profit Factor, Sharpe,
          drawdown, etc.).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Sin métricas reportadas.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Métrica</TableHead>
                <TableHead>Valor</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map(([key, value]) => (
                <TableRow key={key}>
                  <TableCell className="font-mono text-xs">{key}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatScalar(value)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function WorkerInsightsCard({
  insights,
}: {
  insights: WorkerInsightEntry[];
}): React.JSX.Element {
  return (
    <Card data-testid="report-worker-insights">
      <CardHeader>
        <CardTitle>Worker — Insights</CardTitle>
        <CardDescription>
          Reflexiones estructuradas que el Worker generó durante el sueño.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {insights.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Sin insights.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {insights.map((ins, idx) => (
              <li
                key={idx}
                data-testid={`worker-insight-${idx}`}
                className="rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] p-3 text-sm"
              >
                {ins.title ? (
                  <p className="font-medium">{ins.title}</p>
                ) : null}
                {ins.body ? (
                  <p className="text-xs text-[rgb(var(--foreground-muted))]">
                    {ins.body}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ImprovementsCard({
  improvements,
}: {
  improvements: ImprovementEntry[];
}): React.JSX.Element {
  return (
    <Card data-testid="report-improvements">
      <CardHeader>
        <CardTitle>Mejoras aplicadas</CardTitle>
        <CardDescription>
          Cambios decididos por el Orquestador con su nivel de riesgo.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {improvements.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Sin mejoras aplicadas en este run.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {improvements.map((imp, idx) => (
              <li
                key={idx}
                data-testid={`improvement-${idx}`}
                className="flex flex-col gap-1 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  {imp.title ? (
                    <span className="text-sm font-medium">{imp.title}</span>
                  ) : null}
                  <Badge variant={riskVariant(imp.risk)}>
                    {riskLabel(imp.risk)}
                  </Badge>
                </div>
                {imp.description ? (
                  <p className="text-xs text-[rgb(var(--foreground-muted))]">
                    {imp.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function QTableVersionsCard({
  accountId,
  pairId,
  before,
  after,
}: {
  accountId: string;
  pairId: string;
  before: number | null;
  after: number | null;
}): React.JSX.Element {
  return (
    <Card data-testid="report-qtable-versions">
      <CardHeader>
        <CardTitle>Q-Table — antes → después</CardTitle>
        <CardDescription>
          Versiones del Q-Table antes y después de este sueño. Haz clic
          para inspeccionar el contenido.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-2">
        <Link
          href={
            before !== null
              ? `/cuentas/${accountId}/pares/${pairId}/q-tables#v${before}`
              : `/cuentas/${accountId}/pares/${pairId}/q-tables`
          }
          className="rounded-md border border-[rgb(var(--border))] px-3 py-1 text-sm hover:bg-[rgb(var(--background-elevated))]"
          data-testid="qtable-before-link"
        >
          v{before ?? "—"} (antes)
        </Link>
        <span className="text-sm text-[rgb(var(--foreground-muted))]">→</span>
        <Link
          href={
            after !== null
              ? `/cuentas/${accountId}/pares/${pairId}/q-tables#v${after}`
              : `/cuentas/${accountId}/pares/${pairId}/q-tables`
          }
          className="rounded-md border border-[rgb(var(--border))] px-3 py-1 text-sm hover:bg-[rgb(var(--background-elevated))]"
          data-testid="qtable-after-link"
        >
          v{after ?? "—"} (después)
        </Link>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function readObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function readArray<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  return [];
}

function readNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.length > 0 && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toString();
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function overallScoreVariant(
  score: number | null,
): "success" | "warning" | "danger" | "muted" {
  if (score === null) return "muted";
  if (score >= 0.7) return "success";
  if (score >= 0.4) return "warning";
  return "danger";
}

function riskVariant(
  risk: unknown,
): "success" | "warning" | "danger" | "muted" {
  if (risk === "bajo") return "success";
  if (risk === "medio") return "warning";
  if (risk === "alto") return "danger";
  return "muted";
}

function riskLabel(risk: unknown): string {
  if (risk === "bajo" || risk === "medio" || risk === "alto") {
    return RISK_CLASS_LABEL[risk as ConfigVersionRiskClass];
  }
  return "—";
}

function BackLink({ accountId, pairId }: { accountId: string; pairId: string }): React.JSX.Element {
  return (
    <Link
      href={`/cuentas/${accountId}/pares/${pairId}/sleep-runs`}
      className="inline-flex items-center gap-1 text-sm text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]"
    >
      <ArrowLeft className="h-3 w-3" /> Volver a sleep runs
    </Link>
  );
}
