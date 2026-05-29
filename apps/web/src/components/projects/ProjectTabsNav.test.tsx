/**
 * ProjectTabsNav tests — renders all three top-level tabs as links and
 * marks the active one based on the current pathname.
 *
 * Pathname is mocked per `describe` block so we can exercise each branch
 * without exercising the Next.js router.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

// Per-test pathname holder. We swap it before each render below.
let mockedPathname = `/proyectos/${PROJECT_ID}/configuracion`;

vi.mock("next/navigation", () => ({
  usePathname: () => mockedPathname,
}));

import { ProjectTabsNav } from "./ProjectTabsNav";

describe("ProjectTabsNav", () => {
  it("renders the three top-level tab links with correct hrefs", () => {
    mockedPathname = `/proyectos/${PROJECT_ID}/configuracion`;
    render(<ProjectTabsNav projectId={PROJECT_ID} />);

    const operativa = screen.getByTestId("project-tab-operativa");
    const chat = screen.getByTestId("project-tab-chat");
    const config = screen.getByTestId("project-tab-configuracion");

    expect(operativa).toHaveAttribute(
      "href",
      `/proyectos/${PROJECT_ID}/operativa`,
    );
    expect(chat).toHaveAttribute("href", `/proyectos/${PROJECT_ID}/chat`);
    expect(config).toHaveAttribute(
      "href",
      `/proyectos/${PROJECT_ID}/configuracion`,
    );
  });

  it("marks Configuración active when the pathname is /configuracion", () => {
    mockedPathname = `/proyectos/${PROJECT_ID}/configuracion`;
    render(<ProjectTabsNav projectId={PROJECT_ID} />);
    expect(screen.getByTestId("project-tab-configuracion")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByTestId("project-tab-operativa"),
    ).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("project-tab-chat")).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("marks Operativa active when the pathname is /operativa", () => {
    mockedPathname = `/proyectos/${PROJECT_ID}/operativa`;
    render(<ProjectTabsNav projectId={PROJECT_ID} />);
    expect(screen.getByTestId("project-tab-operativa")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("marks Chat active when the pathname is /chat", () => {
    mockedPathname = `/proyectos/${PROJECT_ID}/chat`;
    render(<ProjectTabsNav projectId={PROJECT_ID} />);
    expect(screen.getByTestId("project-tab-chat")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("leaves all three tabs inactive when on a sub-route like /memoria", () => {
    mockedPathname = `/proyectos/${PROJECT_ID}/memoria`;
    render(<ProjectTabsNav projectId={PROJECT_ID} />);
    expect(
      screen.getByTestId("project-tab-operativa"),
    ).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("project-tab-chat")).not.toHaveAttribute(
      "aria-current",
    );
    expect(
      screen.getByTestId("project-tab-configuracion"),
    ).not.toHaveAttribute("aria-current");
  });
});
