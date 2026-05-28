"use client";

import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { mfaSetup, mfaVerify } from "@/lib/mfa";

/**
 * Three-step TOTP enrolment wizard:
 *
 *   1. ``intro``     — explain what MFA is and what's about to happen.
 *   2. ``scan``      — show the QR (qrcode.react SVG) + manual-entry
 *                      secret + the 6-digit verify input.
 *   3. ``recovery``  — show the 10 plaintext recovery codes with a
 *                      download/copy/print path. Closing the dialog
 *                      requires the user to flip the "I've saved them"
 *                      acknowledgement, otherwise the recovery codes
 *                      would be lost forever.
 */

type Step = "intro" | "scan" | "recovery";

interface MfaSetupDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCompleted: () => void;
}

export function MfaSetupDialog({
  open,
  onOpenChange,
  onCompleted,
}: MfaSetupDialogProps): React.JSX.Element {
  const [step, setStep] = useState<Step>("intro");
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [secretB32, setSecretB32] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  // Reset internal state every time the dialog reopens — otherwise an
  // abandoned enrolment would leak old QR data into the next attempt.
  useEffect(() => {
    if (!open) {
      setStep("intro");
      setProvisioningUri(null);
      setSecretB32(null);
      setCode("");
      setRecoveryCodes(null);
      setSaved(false);
      setBusy(false);
    }
  }, [open]);

  async function handleStart(): Promise<void> {
    setBusy(true);
    try {
      const data = await mfaSetup();
      setProvisioningUri(data.provisioning_uri);
      setSecretB32(data.secret_b32);
      setStep("scan");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("MFA ya está habilitado en esta cuenta.");
      } else {
        toast.error("No se pudo iniciar la configuración de MFA.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify(): Promise<void> {
    setBusy(true);
    try {
      const data = await mfaVerify(code.trim());
      setRecoveryCodes(data.recovery_codes);
      setStep("recovery");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        toast.error("Código TOTP incorrecto. Inténtalo de nuevo.");
      } else {
        toast.error("No se pudo verificar el código TOTP.");
      }
    } finally {
      setBusy(false);
    }
  }

  function handleClose(): void {
    if (step === "recovery" && !saved) {
      toast.error(
        "Confirma que has guardado los códigos de recuperación antes de cerrar.",
      );
      return;
    }
    if (step === "recovery") {
      onCompleted();
    }
    onOpenChange(false);
  }

  function downloadCodes(): void {
    if (!recoveryCodes) return;
    const text = [
      "Aether Trading System — Códigos de recuperación MFA",
      "Guarda este archivo en un lugar seguro. Cada código es de un solo uso.",
      "",
      ...recoveryCodes,
    ].join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "aether-mfa-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent>
        {step === "intro" ? (
          <>
            <DialogHeader>
              <DialogTitle>Activar autenticación en dos pasos</DialogTitle>
              <DialogDescription>
                Vamos a vincular tu cuenta a una app de autenticación
                (Google Authenticator, 1Password, Authy...) usando un código
                TOTP de 6 dígitos.
              </DialogDescription>
            </DialogHeader>
            <ul className="list-disc space-y-1 pl-5 text-sm text-[rgb(var(--foreground-muted))]">
              <li>Verás un QR para escanear con tu app.</li>
              <li>Confirmarás con un código de 6 dígitos.</li>
              <li>Te entregaremos 10 códigos de recuperación de un solo uso.</li>
            </ul>
            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={busy}
              >
                Cancelar
              </Button>
              <Button type="button" onClick={() => void handleStart()} disabled={busy}>
                {busy ? "Generando..." : "Empezar"}
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {step === "scan" && provisioningUri && secretB32 ? (
          <>
            <DialogHeader>
              <DialogTitle>Escanea el QR</DialogTitle>
              <DialogDescription>
                Escanea con tu app de autenticación. Si no puedes escanear,
                introduce la clave manualmente.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col items-center gap-4">
              <div className="rounded bg-white p-3">
                <QRCodeSVG value={provisioningUri} size={192} />
              </div>
              <div className="w-full">
                <Label htmlFor="mfa-secret">Clave manual</Label>
                <Input
                  id="mfa-secret"
                  value={secretB32}
                  readOnly
                  onFocus={(e) => e.currentTarget.select()}
                  className="font-mono"
                />
              </div>
              <div className="w-full">
                <Label htmlFor="mfa-code">Código de verificación</Label>
                <Input
                  id="mfa-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={code}
                  onChange={(e) =>
                    setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  className="font-mono tracking-widest"
                />
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={busy}
              >
                Cancelar
              </Button>
              <Button
                type="button"
                onClick={() => void handleVerify()}
                disabled={busy || code.length !== 6}
              >
                {busy ? "Verificando..." : "Verificar"}
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {step === "recovery" && recoveryCodes ? (
          <>
            <DialogHeader>
              <DialogTitle>Códigos de recuperación</DialogTitle>
              <DialogDescription>
                Guarda estos códigos en un lugar seguro. Cada uno sirve para
                iniciar sesión una sola vez si pierdes el acceso a tu app
                de autenticación. NO los volverás a ver.
              </DialogDescription>
            </DialogHeader>
            <ul
              aria-label="Códigos de recuperación"
              className="grid grid-cols-2 gap-2 rounded border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-3 font-mono text-sm"
            >
              {recoveryCodes.map((c) => (
                <li key={c} className="select-all">
                  {c}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={downloadCodes}>
                Descargar .txt
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => window.print()}
              >
                Imprimir
              </Button>
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-[rgb(var(--foreground))]">
              <input
                type="checkbox"
                checked={saved}
                onChange={(e) => setSaved(e.target.checked)}
                className="h-4 w-4"
              />
              He guardado los códigos en un lugar seguro
            </label>
            <DialogFooter>
              <Button type="button" onClick={handleClose} disabled={!saved}>
                Cerrar
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
