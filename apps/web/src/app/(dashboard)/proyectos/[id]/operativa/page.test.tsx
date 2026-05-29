/**
 * Operativa placeholder test — until `project-operativa` lands, the tab
 * is just a "próximamente" card with Spanish copy. Pin both invariants so
 * the placeholder doesn't silently disappear or change language.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import OperativaPage from "./page";

describe("OperativaPage (placeholder)", () => {
  it("renders the próximamente card with Spanish copy", () => {
    render(<OperativaPage />);
    expect(screen.getByTestId("operativa-placeholder")).toBeInTheDocument();
    expect(screen.getByText("Operativa")).toBeInTheDocument();
    expect(
      screen.getByText(/próximamente/i),
    ).toBeInTheDocument();
  });
});
