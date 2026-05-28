import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import LoginPage from "./page";

// Next.js navigation hooks are unavailable outside the App Router runtime.
// Stub them so the component renders in the test environment.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    refresh: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("LoginPage", () => {
  it("renders the login form with email and password inputs", () => {
    render(<LoginPage />);

    expect(
      screen.getByRole("heading", { name: /aether trading system/i }),
    ).toBeInTheDocument();

    const email = screen.getByLabelText(/email/i);
    const password = screen.getByLabelText(/contraseña/i);
    const submit = screen.getByRole("button", { name: /entrar/i });

    expect(email).toBeInTheDocument();
    expect(email).toHaveAttribute("type", "email");
    expect(password).toBeInTheDocument();
    expect(password).toHaveAttribute("type", "password");
    expect(submit).toBeInTheDocument();
    expect(submit).toHaveAttribute("type", "submit");
  });
});
