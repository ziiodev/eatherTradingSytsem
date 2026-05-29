import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import type { ProjectDetail } from "@/lib/projects";

// Mock the data layer — we only care that the panel renders rows and
// opens the dialog when the operator clicks "Editar asignaciones".
vi.mock("@/lib/agents", () => ({
  listAgents: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/lib/projects", async (importOriginal) => {
  const actual =
    await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    patchProject: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { ProjectAgentsPanel } from "./ProjectAgentsPanel";
import { listAgents } from "@/lib/agents";

const baseProject: ProjectDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aether-EURUSD",
  symbol: "EURUSD",
  timeframe: "H1",
  status: "inactive",
  description: null,
  mcp_url: "http://localhost:8081",
  mcp_port: null,
  docker_image: null,
  container_id: null,
  container_name: null,
  account_login: null,
  account_server: null,
  broker_name: null,
  account_credential_ref: null,
  account_currency: null,
  account_leverage: null,
  account_type: null,
  commission_per_lot: null,
  commission_currency: null,
  swap_long: null,
  swap_short: null,
  spread_typical: null,
  capital_asignado: null,
  risk_per_trade: "1.0",
  max_daily_dd: "3.0",
  max_total_dd: "8.0",
  max_exposure: "10.0",
  strategy_version: 1,
  strategy_description: null,
  base_logic: null,
  orchestrator_agent_id: null,
  investigator_agent_id: null,
  marker_agent_id: null,
  worker_agent_id: null,
  tutor_agent_id: null,
  auditor_agent_id: null,
  trading_sessions: [],
  orchestrator_params: {},
  investigator_params: {},
  marker_params: {},
  worker_params: {},
  tutor_params: {},
  auditor_params: {},
  tags: null,
  notes: null,
  error_count: 0,
  last_error: null,
};

describe("ProjectAgentsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it("renders the six slot rows (migration 0012 added Marker + Tutor)", () => {
    render(
      <ProjectAgentsPanel
        project={baseProject}
        onProjectUpdated={vi.fn()}
      />,
    );

    expect(
      screen.getByTestId("project-agents-row-orchestrator"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-agents-row-investigator"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-agents-row-marker"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-agents-row-worker"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-agents-row-tutor"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("project-agents-row-auditor"),
    ).toBeInTheDocument();
  });

  it('shows "No asignado" when slot ids are null', () => {
    render(
      <ProjectAgentsPanel
        project={baseProject}
        onProjectUpdated={vi.fn()}
      />,
    );

    const noneCount = screen.getAllByText(/no asignado/i);
    // One for each of the six slots (Orquestador / Investigador /
    // Marker / Worker / Tutor / Auditor).
    expect(noneCount.length).toBe(6);
  });

  it("opens the dialog with all six pickers when the edit button is clicked", async () => {
    render(
      <ProjectAgentsPanel
        project={baseProject}
        onProjectUpdated={vi.fn()}
      />,
    );

    const button = screen.getByTestId("open-agent-binding-dialog");
    fireEvent.click(button);

    await waitFor(() => {
      expect(
        screen.getByText(/editar asignaciones de agentes/i),
      ).toBeInTheDocument();
    });
    // listAgents must be called six times (one per type) when the
    // dialog opens — Orquestador (0010), Marker + Tutor (0012),
    // plus Investigador / Worker / Auditor.
    await waitFor(() => {
      expect(listAgents).toHaveBeenCalledTimes(6);
    });
    // The six pickers must all be present.
    expect(
      screen.getByTestId("dlg-orchestrator-agent-select"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dlg-investigator-agent-select"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dlg-marker-agent-select"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dlg-worker-agent-select"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dlg-tutor-agent-select"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("dlg-auditor-agent-select"),
    ).toBeInTheDocument();
  });
});
