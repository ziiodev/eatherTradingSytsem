/**
 * Memoria page tests — tab switch + rendering + empty state.
 */

import { Suspense } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/sleep", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchEpisodicMemory: vi.fn(),
    fetchSemanticMemory: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () =>
    "/cuentas/00000000-0000-0000-0000-000000000000/pares/11111111-1111-1111-1111-111111111111/memoria",
}));

import MemoriaPage from "./page";
import { fetchEpisodicMemory, fetchSemanticMemory } from "@/lib/sleep";

const ACCOUNT_ID = "00000000-0000-0000-0000-000000000000";
const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

function resolvedParams<T>(value: T): Promise<T> {
  const p = Promise.resolve(value) as Promise<T> & {
    status?: string;
    value?: T;
  };
  p.status = "fulfilled";
  p.value = value;
  return p;
}

describe("MemoriaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the episodica tab by default and renders rows", async () => {
    (fetchEpisodicMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        {
          id: "ee000001-0000-0000-0000-000000000001",
          pair_id: PROJECT_ID,
          state_key: "stateA1234567890",
          action: "buy",
          reward: "0.025",
          next_state_key: null,
          order_id: null,
          consumed_by_sleep_run_id: null,
          meta_data: { is_special: true },
          created_at: "2026-05-28T12:00:00",
        },
        {
          id: "ee000002-0000-0000-0000-000000000002",
          pair_id: PROJECT_ID,
          state_key: "stateB",
          action: "sell",
          reward: "-0.01",
          next_state_key: null,
          order_id: null,
          consumed_by_sleep_run_id: null,
          meta_data: {},
          created_at: "2026-05-28T13:00:00",
        },
      ],
      total: 2,
    });
    (fetchSemanticMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });

    render(
      <Suspense fallback={<div>L</div>}>
        <MemoriaPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    await waitFor(() => {
      expect(
        screen.getByTestId("episodica-row-ee000001-0000-0000-0000-000000000001"),
      ).toBeInTheDocument();
    });
    // Special badge for the flagged episode.
    expect(screen.getAllByText(/special/i).length).toBeGreaterThan(0);
  });

  it("switches to the semantica tab and renders grouped rules", async () => {
    (fetchEpisodicMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });
    (fetchSemanticMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        {
          id: "ss000001-0000-0000-0000-000000000001",
          pair_id: PROJECT_ID,
          rule_type: "avoid_state",
          body: "no operar en estado X",
          payload: { confidence: 0.8, source: "auditor" },
          superseded_by: null,
          active: true,
          created_by_sleep_run_id: null,
          created_at: "2026-05-28T10:00:00",
          updated_at: null,
        },
        {
          id: "ss000002-0000-0000-0000-000000000002",
          pair_id: PROJECT_ID,
          rule_type: "tighten_sl",
          body: "stop-loss más agresivo en sesión asiática",
          payload: {},
          superseded_by: null,
          active: true,
          created_by_sleep_run_id: null,
          created_at: "2026-05-28T11:00:00",
          updated_at: null,
        },
      ],
      total: 2,
    });

    render(
      <Suspense fallback={<div>L</div>}>
        <MemoriaPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    // Click the Semantica tab.
    fireEvent.click(screen.getByTestId("memoria-tab-semantica"));

    await waitFor(() => {
      expect(
        screen.getByTestId("semantica-group-avoid_state"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("semantica-group-tighten_sl"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/no operar en estado X/i),
    ).toBeInTheDocument();
  });

  it("renders semantica empty state when no rules", async () => {
    (fetchEpisodicMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });
    (fetchSemanticMemory as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });

    render(
      <Suspense fallback={<div>L</div>}>
        <MemoriaPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    fireEvent.click(screen.getByTestId("memoria-tab-semantica"));

    await waitFor(() => {
      expect(screen.getByTestId("semantica-empty")).toBeInTheDocument();
    });
  });
});
