/**
 * LearningNav tests — env-flag gating + active-state styling.
 *
 * `NEXT_PUBLIC_*` is build-time-baked in real Next builds, but vitest
 * reads `process.env` at call time so we can flip it per test.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  usePathname: () => "/cuentas/acc/pares/abc/q-tables",
}));

import { LearningNav, isLearningUiEnabled } from "./LearningNav";

const ACCOUNT_ID = "00000000-0000-0000-0000-000000000000";
const PAIR_ID = "11111111-1111-1111-1111-111111111111";

const original = process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED;

afterEach(() => {
  if (original === undefined) {
    delete process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED;
  } else {
    process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED = original;
  }
});

describe("LearningNav", () => {
  it("renders nothing when the flag is unset", () => {
    delete process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED;
    expect(isLearningUiEnabled()).toBe(false);
    const { container } = render(<LearningNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the flag is not exactly 'true'", () => {
    process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED = "1";
    expect(isLearningUiEnabled()).toBe(false);
    const { container } = render(<LearningNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the three learning links when the flag is 'true'", () => {
    process.env.NEXT_PUBLIC_LEARNING_UI_ENABLED = "true";
    expect(isLearningUiEnabled()).toBe(true);
    render(<LearningNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(screen.getByText("Q-Tables")).toBeInTheDocument();
    expect(screen.getByText("Memoria")).toBeInTheDocument();
    expect(screen.getByText("Sleep Runs")).toBeInTheDocument();
    expect(screen.getByText("Q-Tables").closest("a")).toHaveAttribute(
      "href",
      `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/q-tables`,
    );
  });
});
