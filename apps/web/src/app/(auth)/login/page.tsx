"use client";

import { useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { apiPost, ApiError } from "@/lib/api";
import { loginWithMfa } from "@/lib/mfa";
import { validateReturnTo } from "@/lib/safe-redirect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface LoginResponse {
  user: { id: string; email: string } | null;
  requires_mfa: boolean;
}

type Step = "credentials" | "mfa";
type SecondFactor = "totp" | "recovery";

export default function LoginPage(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  // `validateReturnTo` is the single sanctioned open-redirect guard — see
  // `apps/web/src/lib/safe-redirect.ts` and `specs/auth`.
  const returnTo = validateReturnTo(searchParams.get("return_to"));

  const [step, setStep] = useState<Step>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [secondFactor, setSecondFactor] = useState<SecondFactor>("totp");
  const [totpCode, setTotpCode] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleNavigate(): void {
    router.push(returnTo);
    router.refresh();
  }

  async function handleCredentials(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const resp = await apiPost<LoginResponse>("/api/auth/login", {
        email: email.trim().toLowerCase(),
        password,
      });
      if (resp.requires_mfa) {
        // The backend set ``aether_mfa_pending`` (path-scoped to
        // /api/auth/login/mfa). No session cookies yet. Reveal the
        // second-step input form; the password field is wiped so a
        // shoulder-surfer can't grab it from autocomplete.
        setPassword("");
        setStep("mfa");
      } else {
        handleNavigate();
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Credenciales inválidas. Verifica tu email y contraseña.");
      } else if (err instanceof ApiError && err.status === 423) {
        setError("Cuenta bloqueada temporalmente. Inténtalo más tarde.");
      } else {
        setError("No se pudo iniciar sesión. Inténtalo de nuevo.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleMfa(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (secondFactor === "totp") {
        await loginWithMfa({ totp_code: totpCode.trim() });
      } else {
        await loginWithMfa({ recovery_code: recoveryCode.trim() });
      }
      setTotpCode("");
      setRecoveryCode("");
      handleNavigate();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError(
          "Código inválido o sesión expirada. Vuelve a empezar el login.",
        );
      } else {
        setError("No se pudo verificar el código. Inténtalo de nuevo.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Aether Trading System</CardTitle>
        <CardDescription>
          {step === "credentials"
            ? "Inicia sesión para acceder al panel de control."
            : "Introduce el código de tu app de autenticación."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {step === "credentials" ? (
          <form
            onSubmit={handleCredentials}
            className="flex flex-col gap-4"
            aria-describedby={error ? "login-error" : undefined}
            noValidate
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Contraseña</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />
            </div>
            {error && (
              <p
                id="login-error"
                role="alert"
                className="text-sm text-[rgb(var(--danger))]"
              >
                {error}
              </p>
            )}
            <Button type="submit" disabled={submitting} className="mt-2">
              {submitting ? "Entrando…" : "Entrar"}
            </Button>
          </form>
        ) : (
          <form
            onSubmit={handleMfa}
            className="flex flex-col gap-4"
            aria-describedby={error ? "login-error" : undefined}
            noValidate
          >
            <div
              role="radiogroup"
              aria-label="Tipo de código"
              className="flex gap-2 text-sm"
            >
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name="second-factor"
                  value="totp"
                  checked={secondFactor === "totp"}
                  onChange={() => setSecondFactor("totp")}
                  disabled={submitting}
                />
                TOTP (app)
              </label>
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name="second-factor"
                  value="recovery"
                  checked={secondFactor === "recovery"}
                  onChange={() => setSecondFactor("recovery")}
                  disabled={submitting}
                />
                Código de recuperación
              </label>
            </div>

            {secondFactor === "totp" ? (
              <div className="flex flex-col gap-2">
                <Label htmlFor="totp">Código de 6 dígitos</Label>
                <Input
                  id="totp"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  value={totpCode}
                  onChange={(e) =>
                    setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  className="font-mono tracking-widest"
                  disabled={submitting}
                />
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <Label htmlFor="recovery">Código de recuperación</Label>
                <Input
                  id="recovery"
                  type="text"
                  autoComplete="off"
                  required
                  value={recoveryCode}
                  onChange={(e) => setRecoveryCode(e.target.value)}
                  className="font-mono"
                  disabled={submitting}
                />
              </div>
            )}

            {error && (
              <p
                id="login-error"
                role="alert"
                className="text-sm text-[rgb(var(--danger))]"
              >
                {error}
              </p>
            )}

            <Button
              type="submit"
              disabled={
                submitting ||
                (secondFactor === "totp"
                  ? totpCode.length !== 6
                  : recoveryCode.trim().length === 0)
              }
              className="mt-2"
            >
              {submitting ? "Verificando…" : "Verificar"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setStep("credentials");
                setError(null);
                setTotpCode("");
                setRecoveryCode("");
              }}
              disabled={submitting}
            >
              Volver
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
