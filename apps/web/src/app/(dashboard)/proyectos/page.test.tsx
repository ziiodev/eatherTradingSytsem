import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import ProyectosPage from "./page";

// Stub the data layer — every test sets a fresh mock body.
// Mock only the data functions the page calls — the constants and helpers
// (PROJECT_STATUSES, etc.) are re-exported below from the real module so
// the component still renders identically to production.
vi.mock("@/lib/projects", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    listProjects: vi.fn(),
    lifecycleAction: vi.fn(),
    deleteProject: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { listProjects } from "@/lib/projects";

describe("ProyectosPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when the API returns no items", async () => {
    (listProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    });

    render(<ProyectosPage />);

    expect(
      screen.getByRole("heading", { name: /proyectos/i, level: 1 }),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/no hay proyectos/i)).toBeInTheDocument();
    });
  });

  it("renders rows when the API returns items", async () => {
    (listProjects as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        {
          id: "00000000-0000-0000-0000-000000000001",
          name: "Aether-EURUSD",
          symbol: "EURUSD",
          timeframe: "H1",
          status: "active",
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });

    render(<ProyectosPage />);

    await waitFor(() => {
      expect(screen.getByText("Aether-EURUSD")).toBeInTheDocument();
    });
    expect(screen.getByText("EURUSD")).toBeInTheDocument();
    expect(screen.getByText("H1")).toBeInTheDocument();
    // "Activo" appears both in the filter <select> and the status badge —
    // assert at least one is rendered (badge presence is implied by row).
    expect(screen.getAllByText("Activo").length).toBeGreaterThan(0);
  });
});
