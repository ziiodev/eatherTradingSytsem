/**
 * Q-Tables page — rendering + selection interaction.
 */

import { Suspense } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("@/lib/sleep", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchQTables: vi.fn(),
    fetchQTable: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () =>
    "/cuentas/00000000-0000-0000-0000-000000000000/pares/11111111-1111-1111-1111-111111111111/q-tables",
}));

import QTablesPage from "./page";
import { fetchQTable, fetchQTables } from "@/lib/sleep";

const ACCOUNT_ID = "00000000-0000-0000-0000-000000000000";
const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

/**
 * React 19 `use()` reads a thenable with `_status === "fulfilled"` and
 * `_result` set synchronously, so the page does not suspend in tests.
 * (Plain Promise.resolve(...) leaves status undefined → React suspends
 * forever in our happy-dom setup.)
 */
function resolvedParams<T>(value: T): Promise<T> {
  const p = Promise.resolve(value) as Promise<T> & {
    status?: string;
    value?: T;
  };
  p.status = "fulfilled";
  p.value = value;
  return p;
}

function makeListItem(version: number) {
  return {
    id: `aaaaaaaa-0000-0000-0000-00000000000${version}`,
    pair_id: PROJECT_ID,
    version,
    alpha_normal: "0.15",
    alpha_special: "0.35",
    gamma: "0.92",
    episode_count: version * 10,
    created_by_sleep_run_id: null,
    created_at: "2026-05-28T10:00:00",
  };
}

function makeDetail(
  version: number,
  table_data: Record<string, Record<string, number>>,
) {
  return { ...makeListItem(version), table_data };
}

describe("QTablesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the version list and selects the newest by default", async () => {
    (fetchQTables as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeListItem(2), makeListItem(1)],
      total: 2,
    });
    (fetchQTable as ReturnType<typeof vi.fn>).mockImplementation(
      async (_id: string, v: number) =>
        makeDetail(v, { s1: { buy: v, sell: 0 } }),
    );

    render(
      <Suspense fallback={<div>loading…</div>}>
        <QTablesPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    // Both rows visible.
    await waitFor(() => {
      expect(screen.getByTestId("q-table-row-2")).toBeInTheDocument();
    });
    expect(screen.getByTestId("q-table-row-1")).toBeInTheDocument();

    // table_data of v2 is fetched on mount + diff card eventually visible.
    await waitFor(() => {
      expect(screen.getByTestId("q-table-json")).toBeInTheDocument();
    });
    expect(screen.getByTestId("q-table-json").textContent).toContain("s1");
  });

  it("reloads the detail when a different version is selected", async () => {
    (fetchQTables as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeListItem(3), makeListItem(2), makeListItem(1)],
      total: 3,
    });
    (fetchQTable as ReturnType<typeof vi.fn>).mockImplementation(
      async (_id: string, v: number) =>
        makeDetail(v, { [`state-v${v}`]: { buy: 1, sell: 0 } }),
    );

    render(
      <Suspense fallback={<div>loading…</div>}>
        <QTablesPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("q-table-json")).toBeInTheDocument();
    });
    // Default selection = newest (v3) → JSON contains state-v3.
    expect(screen.getByTestId("q-table-json").textContent).toContain(
      "state-v3",
    );

    // Switch to v1.
    fireEvent.click(screen.getByTestId("q-table-select-1"));

    await waitFor(() => {
      expect(screen.getByTestId("q-table-json").textContent).toContain(
        "state-v1",
      );
    });
  });

  it("renders an empty state when the project has no versions", async () => {
    (fetchQTables as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });

    render(
      <Suspense fallback={<div>loading…</div>}>
        <QTablesPage params={resolvedParams({ accountId: ACCOUNT_ID, pairId: PROJECT_ID })} />
      </Suspense>,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/no tiene versiones de Q-Table/i),
      ).toBeInTheDocument();
    });
  });
});
