/**
 * Sleep Run report viewer — renders every section from a mocked payload.
 */

import { Suspense } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/sleep", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    getSleepRun: vi.fn(),
    fetchSleepReport: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () =>
    "/proyectos/11111111-1111-1111-1111-111111111111/sleep-runs/22222222-2222-2222-2222-222222222222",
}));

import SleepRunReportPage from "./page";
import { fetchSleepReport, getSleepRun } from "@/lib/sleep";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const RUN_ID = "22222222-2222-2222-2222-222222222222";

function resolvedParams<T>(value: T): Promise<T> {
  const p = Promise.resolve(value) as Promise<T> & {
    status?: string;
    value?: T;
  };
  p.status = "fulfilled";
  p.value = value;
  return p;
}

describe("SleepRunReportPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders every section from a populated report", async () => {
    (getSleepRun as ReturnType<typeof vi.fn>).mockResolvedValue({
      run: {
        id: RUN_ID,
        project_id: PROJECT_ID,
        phase_type: "profundo",
        status: "succeeded",
        started_at: "2026-05-28T01:00:00",
        ended_at: "2026-05-28T01:45:00",
        summary: "ok",
        error: null,
      },
      reflections: [],
      config_versions: [],
    });
    (fetchSleepReport as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "66666666-6666-6666-6666-666666666666",
      sleep_run_id: RUN_ID,
      payload: {
        overall_score: 0.82,
        auditor_metrics: {
          profit_factor: 1.45,
          max_drawdown: -0.04,
          sharpe: 1.1,
        },
        worker_insights: [
          { title: "Bias alcista", body: "predominio en sesión europea" },
          { title: "Slippage", body: "elevado en cierre de Tokio" },
        ],
        improvements_applied: [
          {
            title: "Reducir tamaño de lote",
            description: "0.10 → 0.08",
            risk: "bajo",
          },
          {
            title: "Cambiar TF a M15",
            description: "evita ruido en H1",
            risk: "alto",
          },
        ],
        q_table_before: 4,
        q_table_after: 5,
      },
      summary_md: "## Resumen\nDecisión OK.",
      created_at: "2026-05-28T01:50:00",
    });

    render(
      <Suspense fallback={<div>L</div>}>
        <SleepRunReportPage
          params={resolvedParams({ id: PROJECT_ID, runId: RUN_ID })}
        />
      </Suspense>,
    );

    // Header
    await waitFor(() => {
      expect(screen.getByTestId("report-header")).toBeInTheDocument();
    });
    expect(screen.getByText(/score 0\.82/i)).toBeInTheDocument();

    // Auditor metrics
    expect(screen.getByTestId("report-auditor-metrics")).toBeInTheDocument();
    expect(screen.getByText("profit_factor")).toBeInTheDocument();
    expect(screen.getByText("1.45")).toBeInTheDocument();

    // Worker insights
    expect(screen.getByTestId("worker-insight-0")).toBeInTheDocument();
    expect(screen.getByTestId("worker-insight-1")).toBeInTheDocument();
    expect(screen.getByText(/Bias alcista/i)).toBeInTheDocument();

    // Improvements with risk badges
    expect(screen.getByTestId("improvement-0")).toBeInTheDocument();
    expect(screen.getByTestId("improvement-1")).toBeInTheDocument();
    expect(screen.getByText(/Bajo/)).toBeInTheDocument();
    expect(screen.getByText(/Alto/)).toBeInTheDocument();

    // Q-Table before/after links
    const beforeLink = screen.getByTestId("qtable-before-link");
    const afterLink = screen.getByTestId("qtable-after-link");
    expect(beforeLink).toHaveAttribute(
      "href",
      `/proyectos/${PROJECT_ID}/q-tables#v4`,
    );
    expect(afterLink).toHaveAttribute(
      "href",
      `/proyectos/${PROJECT_ID}/q-tables#v5`,
    );

    // Summary markdown
    expect(screen.getByTestId("report-summary-md")).toBeInTheDocument();
  });

  it("renders empty placeholders when payload is sparse", async () => {
    (getSleepRun as ReturnType<typeof vi.fn>).mockResolvedValue({
      run: {
        id: RUN_ID,
        project_id: PROJECT_ID,
        phase_type: "micro",
        status: "succeeded",
        started_at: null,
        ended_at: null,
        summary: null,
        error: null,
      },
      reflections: [],
      config_versions: [],
    });
    (fetchSleepReport as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "66666666-6666-6666-6666-666666666666",
      sleep_run_id: RUN_ID,
      payload: {},
      summary_md: null,
      created_at: null,
    });

    render(
      <Suspense fallback={<div>L</div>}>
        <SleepRunReportPage
          params={resolvedParams({ id: PROJECT_ID, runId: RUN_ID })}
        />
      </Suspense>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("sleep-report-section")).toBeInTheDocument();
    });
    expect(screen.getByText(/sin score/i)).toBeInTheDocument();
    expect(screen.getByText(/sin métricas reportadas/i)).toBeInTheDocument();
    expect(screen.getByText(/sin insights/i)).toBeInTheDocument();
    expect(screen.getByText(/sin mejoras aplicadas/i)).toBeInTheDocument();
  });
});
