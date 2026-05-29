/**
 * ConfiguracionTab tests — once the project has loaded the three inner
 * tabs (General / Infraestructura / Sueño) must all be rendered as
 * shadcn-Tabs triggers.
 *
 * History: the prior `operativa` sub-tab was REMOVED in `project-operativa`
 * (Phase 7.2). The realtime Operativa surface is now its own top-level
 * tab and Configuración no longer hosts a duplicate.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import type { ProjectDetail } from "@/lib/projects";

vi.mock("@/lib/projects", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    getProject: vi.fn(),
    patchProject: vi.fn(),
  };
});

vi.mock("@/lib/agents", () => ({
  listAgents: vi.fn().mockResolvedValue([]),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

// Panels we don't care about exercising in this test — keep them light so
// the test focuses on the tab structure rather than each panel's own
// data fetching behavior.
vi.mock("@/components/projects/InfraestructuraPanel", () => ({
  InfraestructuraPanel: () => <div data-testid="infra-panel-stub" />,
}));
vi.mock("@/components/projects/SuenoPanel", () => ({
  SuenoPanel: () => <div data-testid="sueno-panel-stub" />,
}));

import { ConfiguracionTab } from "./ConfiguracionTab";
import { getProject } from "@/lib/projects";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

const baseProject: ProjectDetail = {
  id: PROJECT_ID,
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

describe("ConfiguracionTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getProject as ReturnType<typeof vi.fn>).mockResolvedValue(baseProject);
  });

  it("renders the three inner sub-tab triggers once the project loads", async () => {
    render(<ConfiguracionTab projectId={PROJECT_ID} />);

    await waitFor(() => {
      expect(
        screen.getByTestId("config-subtab-general"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("config-subtab-infraestructura"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("config-subtab-sueno")).toBeInTheDocument();
    expect(screen.getByTestId("config-subtab-sueno")).toHaveTextContent(
      "Sueño",
    );
  });

  it("no longer mounts an Operativa sub-tab (project-operativa Phase 7.2)", async () => {
    render(<ConfiguracionTab projectId={PROJECT_ID} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("config-subtab-general"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("config-subtab-operativa"),
    ).not.toBeInTheDocument();
  });
});
