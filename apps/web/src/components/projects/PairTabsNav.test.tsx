/**
 * PairTabsNav tests — renders all three top-level tabs as links and
 * marks the active one based on the current pathname.
 *
 * Pathname is mocked per `describe` block so we can exercise each branch
 * without exercising the Next.js router.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const ACCOUNT_ID = "00000000-0000-0000-0000-000000000000";
const PAIR_ID = "11111111-1111-1111-1111-111111111111";

// Per-test pathname holder. We swap it before each render below.
let mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/configuracion`;

vi.mock("next/navigation", () => ({
  usePathname: () => mockedPathname,
}));

import { PairTabsNav } from "./PairTabsNav";

describe("PairTabsNav", () => {
  it("renders the three top-level tab links with correct hrefs", () => {
    mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/configuracion`;
    render(<PairTabsNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);

    const operativa = screen.getByTestId("pair-tab-operativa");
    const chat = screen.getByTestId("pair-tab-chat");
    const config = screen.getByTestId("pair-tab-configuracion");

    expect(operativa).toHaveAttribute(
      "href",
      `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/operativa`,
    );
    expect(chat).toHaveAttribute("href", `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/chat`);
    expect(config).toHaveAttribute(
      "href",
      `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/configuracion`,
    );
  });

  it("marks Configuración active when the pathname is /configuracion", () => {
    mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/configuracion`;
    render(<PairTabsNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(screen.getByTestId("pair-tab-configuracion")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByTestId("pair-tab-operativa"),
    ).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("pair-tab-chat")).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("marks Operativa active when the pathname is /operativa", () => {
    mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/operativa`;
    render(<PairTabsNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(screen.getByTestId("pair-tab-operativa")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("marks Chat active when the pathname is /chat", () => {
    mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/chat`;
    render(<PairTabsNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(screen.getByTestId("pair-tab-chat")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("leaves all three tabs inactive when on a sub-route like /memoria", () => {
    mockedPathname = `/cuentas/${ACCOUNT_ID}/pares/${PAIR_ID}/memoria`;
    render(<PairTabsNav accountId={ACCOUNT_ID} pairId={PAIR_ID} />);
    expect(
      screen.getByTestId("pair-tab-operativa"),
    ).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("pair-tab-chat")).not.toHaveAttribute(
      "aria-current",
    );
    expect(
      screen.getByTestId("pair-tab-configuracion"),
    ).not.toHaveAttribute("aria-current");
  });
});
