import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";

// Stub the translator client — keeps the test offline + deterministic.
vi.mock("@/lib/translator", () => ({
  translateMql5ToPython: vi.fn(),
}));

// CodeMirror touches a lot of DOM that happy-dom doesn't fully implement.
// Replace it with a minimal stub that renders the value into a textarea
// (read-only when readOnly=true) so we can assert on the produced Python.
vi.mock("@/components/CodeMirrorEditor", () => ({
  CodeMirrorEditor: (props: {
    value: string;
    readOnly?: boolean;
    "data-testid"?: string;
  }) => (
    <textarea
      data-testid={props["data-testid"] ?? "codemirror-stub"}
      value={props.value}
      readOnly={props.readOnly ?? false}
      onChange={() => {
        /* read-only stub */
      }}
    />
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { translateMql5ToPython } from "@/lib/translator";
import { toast } from "sonner";
import { Mql5TranslatorDialog } from "./Mql5TranslatorDialog";

const CANNED_PYTHON =
  "# TODO: review — auto-translated from MQL5.\n" +
  "def on_tick(ctx):\n    return None\n";

function Harness({
  onApply,
}: {
  onApply?: (python: string) => void;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(true);
  return (
    <Mql5TranslatorDialog
      open={open}
      onOpenChange={setOpen}
      onApply={onApply ?? (() => undefined)}
    />
  );
}

// Local React import for the Harness above — keep happy-dom happy.
import * as React from "react";

describe("Mql5TranslatorDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders title, description, and disables Aplicar until translation succeeds", async () => {
    render(<Harness />);

    expect(
      screen.getByRole("heading", { name: /convertir mql5 → python/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/pega tu código mql5\/mql4/i),
    ).toBeInTheDocument();

    const applyBtn = screen.getByTestId("apply-button") as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(true);
  });

  it("calls Aplicar with the translated Python after Convertir succeeds", async () => {
    (translateMql5ToPython as ReturnType<typeof vi.fn>).mockResolvedValue({
      python: CANNED_PYTHON,
      model: "claude-test",
      input_tokens: 10,
      output_tokens: 20,
    });

    const onApply = vi.fn();
    const user = userEvent.setup();
    render(<Harness onApply={onApply} />);

    const textarea = screen.getByTestId("mql5-input") as HTMLTextAreaElement;
    // ``{`` / ``}`` are key descriptors in user-event — wrap them with
    // double braces so the literal characters land in the textarea.
    await user.type(textarea, "void OnTick() {{}}");

    const convertBtn = screen.getByTestId("convert-button");
    await act(async () => {
      await user.click(convertBtn);
    });

    await waitFor(() => {
      expect(translateMql5ToPython).toHaveBeenCalledTimes(1);
    });

    // The python output stub should now hold the translated source.
    await waitFor(() => {
      const out = screen.getByTestId("python-output") as HTMLTextAreaElement;
      expect(out.value).toBe(CANNED_PYTHON);
    });

    const applyBtn = screen.getByTestId("apply-button") as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(false);

    await act(async () => {
      await user.click(applyBtn);
    });
    expect(onApply).toHaveBeenCalledWith(CANNED_PYTHON);
  });

  it("Cancelar closes without calling onApply", async () => {
    const onApply = vi.fn();
    const user = userEvent.setup();
    render(<Harness onApply={onApply} />);

    const cancelBtn = screen.getByTestId("cancel-button");
    await act(async () => {
      await user.click(cancelBtn);
    });

    expect(onApply).not.toHaveBeenCalled();
  });

  it("shows a toast when the translator returns an error", async () => {
    (translateMql5ToPython as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("upstream", 502, {
        detail: { code: "translator_upstream_error" },
      }),
    );

    const user = userEvent.setup();
    render(<Harness />);

    const textarea = screen.getByTestId("mql5-input") as HTMLTextAreaElement;
    // ``{`` / ``}`` are key descriptors in user-event — wrap them with
    // double braces so the literal characters land in the textarea.
    await user.type(textarea, "void OnTick() {{}}");
    await act(async () => {
      await user.click(screen.getByTestId("convert-button"));
    });

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalled();
    });

    // Aplicar must stay disabled when no python was produced.
    const applyBtn = screen.getByTestId("apply-button") as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(true);
  });
});
