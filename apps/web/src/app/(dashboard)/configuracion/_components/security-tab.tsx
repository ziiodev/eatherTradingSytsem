"use client";

import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { MfaSetupDialog } from "@/app/(dashboard)/configuracion/_components/mfa-setup-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ApiError } from "@/lib/api";
import {
  changeEmail,
  changePassword,
  type Me,
} from "@/lib/me";
import { mfaDisable, mfaRegenerateRecoveryCodes } from "@/lib/mfa";

/**
 * Seguridad tab — email change + password change.
 *
 * Both flows require the current password (verified server-side via
 * argon2id). The password form opts the user into "sign out other
 * devices" by default — that matches the charter's anti-theft posture.
 */

const emailSchema = z.object({
  new_email: z.string().email("Email inválido."),
  current_password: z.string().min(1, "La contraseña actual es obligatoria."),
});

const passwordSchema = z
  .object({
    current_password: z
      .string()
      .min(1, "La contraseña actual es obligatoria."),
    new_password: z.string().min(8, "Mínimo 8 caracteres."),
    confirm_password: z.string(),
    sign_out_others: z.boolean(),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    path: ["confirm_password"],
    message: "Las contraseñas no coinciden.",
  });

type EmailFormValues = z.infer<typeof emailSchema>;
type PasswordFormValues = z.infer<typeof passwordSchema>;

interface SecurityTabProps {
  me: Me | null;
  onEmailChanged: (next: Me) => void;
  onMfaChanged: () => void;
}

export function SecurityTab({
  me,
  onEmailChanged,
  onMfaChanged,
}: SecurityTabProps): React.JSX.Element {
  const [mfaDialogOpen, setMfaDialogOpen] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [disablePw, setDisablePw] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [disableBusy, setDisableBusy] = useState(false);
  const [regenPw, setRegenPw] = useState("");
  const [regenBusy, setRegenBusy] = useState(false);
  const [regenCodes, setRegenCodes] = useState<string[] | null>(null);

  // Type-narrowed MFA flag — pre-Me-load we render the section disabled.
  const mfaEnabled = me?.mfa_enabled ?? false;

  async function handleDisable(): Promise<void> {
    setDisableBusy(true);
    try {
      await mfaDisable({
        current_password: disablePw,
        totp_code: disableCode.trim(),
      });
      toast.success("MFA deshabilitado.");
      setDisableOpen(false);
      setDisablePw("");
      setDisableCode("");
      onMfaChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        toast.error("Credenciales o código TOTP incorrectos.");
      } else {
        toast.error("No se pudo deshabilitar MFA.");
      }
    } finally {
      setDisableBusy(false);
    }
  }

  async function handleRegenerate(): Promise<void> {
    setRegenBusy(true);
    try {
      const data = await mfaRegenerateRecoveryCodes(regenPw);
      setRegenCodes(data.recovery_codes);
      setRegenPw("");
      toast.success("Códigos regenerados. Los anteriores quedan inválidos.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        toast.error("Contraseña incorrecta.");
      } else {
        toast.error("No se pudieron regenerar los códigos.");
      }
    } finally {
      setRegenBusy(false);
    }
  }
  const emailForm = useForm<EmailFormValues>({
    resolver: zodResolver(emailSchema),
    defaultValues: { new_email: "", current_password: "" },
  });
  const passwordForm = useForm<PasswordFormValues>({
    resolver: zodResolver(passwordSchema),
    defaultValues: {
      current_password: "",
      new_password: "",
      confirm_password: "",
      sign_out_others: true,
    },
  });

  const submitEmail = async (values: EmailFormValues): Promise<void> => {
    try {
      const updated = await changeEmail(values);
      onEmailChanged(updated);
      emailForm.reset({ new_email: "", current_password: "" });
      toast.success("Email actualizado. Verifica tu nueva dirección.");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          toast.error("Contraseña actual incorrecta.");
          return;
        }
        if (err.status === 409) {
          toast.error("Ese email ya está en uso.");
          return;
        }
      }
      toast.error("No se pudo cambiar el email.");
    }
  };

  const submitPassword = async (
    values: PasswordFormValues,
  ): Promise<void> => {
    try {
      const result = await changePassword({
        current_password: values.current_password,
        new_password: values.new_password,
        sign_out_others: values.sign_out_others,
      });
      passwordForm.reset({
        current_password: "",
        new_password: "",
        confirm_password: "",
        sign_out_others: values.sign_out_others,
      });
      if (result.revoked_other_sessions > 0) {
        toast.success(
          `Contraseña actualizada. Se cerraron ${result.revoked_other_sessions} sesiones en otros dispositivos.`,
        );
      } else {
        toast.success("Contraseña actualizada.");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        toast.error("Contraseña actual incorrecta.");
        return;
      }
      toast.error("No se pudo cambiar la contraseña.");
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Cambiar email</CardTitle>
          <CardDescription>
            Tu nuevo email quedará pendiente de verificación.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              void emailForm.handleSubmit(submitEmail)(e);
            }}
            noValidate
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="new_email">Nuevo email</Label>
              <Input
                id="new_email"
                type="email"
                autoComplete="email"
                {...emailForm.register("new_email")}
                aria-invalid={
                  emailForm.formState.errors.new_email ? "true" : "false"
                }
              />
              {emailForm.formState.errors.new_email ? (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {emailForm.formState.errors.new_email.message}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email_current_password">
                Contraseña actual
              </Label>
              <Input
                id="email_current_password"
                type="password"
                autoComplete="current-password"
                {...emailForm.register("current_password")}
                aria-invalid={
                  emailForm.formState.errors.current_password
                    ? "true"
                    : "false"
                }
              />
              {emailForm.formState.errors.current_password ? (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {emailForm.formState.errors.current_password.message}
                </p>
              ) : null}
            </div>
            <div>
              <Button
                type="submit"
                disabled={emailForm.formState.isSubmitting}
              >
                {emailForm.formState.isSubmitting
                  ? "Guardando..."
                  : "Cambiar email"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Separator />

      <Card>
        <CardHeader>
          <CardTitle>Cambiar contraseña</CardTitle>
          <CardDescription>
            Mínimo 8 caracteres. Te recomendamos cerrar sesión en otros
            dispositivos.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              void passwordForm.handleSubmit(submitPassword)(e);
            }}
            noValidate
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="pw_current">Contraseña actual</Label>
              <Input
                id="pw_current"
                type="password"
                autoComplete="current-password"
                {...passwordForm.register("current_password")}
                aria-invalid={
                  passwordForm.formState.errors.current_password
                    ? "true"
                    : "false"
                }
              />
              {passwordForm.formState.errors.current_password ? (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {passwordForm.formState.errors.current_password.message}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="pw_new">Nueva contraseña</Label>
              <Input
                id="pw_new"
                type="password"
                autoComplete="new-password"
                {...passwordForm.register("new_password")}
                aria-invalid={
                  passwordForm.formState.errors.new_password ? "true" : "false"
                }
              />
              {passwordForm.formState.errors.new_password ? (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {passwordForm.formState.errors.new_password.message}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="pw_confirm">Confirmar nueva contraseña</Label>
              <Input
                id="pw_confirm"
                type="password"
                autoComplete="new-password"
                {...passwordForm.register("confirm_password")}
                aria-invalid={
                  passwordForm.formState.errors.confirm_password
                    ? "true"
                    : "false"
                }
              />
              {passwordForm.formState.errors.confirm_password ? (
                <p className="text-xs text-[rgb(var(--danger))]">
                  {passwordForm.formState.errors.confirm_password.message}
                </p>
              ) : null}
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-[rgb(var(--foreground))]">
              <input
                type="checkbox"
                {...passwordForm.register("sign_out_others")}
                className="h-4 w-4"
              />
              Cerrar sesión en otros dispositivos
            </label>
            <div>
              <Button
                type="submit"
                disabled={passwordForm.formState.isSubmitting}
              >
                {passwordForm.formState.isSubmitting
                  ? "Guardando..."
                  : "Cambiar contraseña"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Separator />

      <Card>
        <CardHeader>
          <CardTitle>Autenticación en dos pasos (MFA)</CardTitle>
          <CardDescription>
            Requiere un código de tu app de autenticación además de la
            contraseña. Obligatorio antes de habilitar cuentas reales.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              Estado:{" "}
              <span className="font-medium text-[rgb(var(--foreground))]">
                {mfaEnabled ? "Activado" : "Desactivado"}
              </span>
            </p>

            {!mfaEnabled ? (
              <div>
                <Button
                  type="button"
                  onClick={() => setMfaDialogOpen(true)}
                  disabled={!me}
                >
                  Activar MFA
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setDisableOpen((v) => !v)}
                  >
                    Desactivar MFA
                  </Button>
                </div>

                {disableOpen ? (
                  <div className="flex flex-col gap-3 rounded border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-3">
                    <Label htmlFor="mfa-dis-pw">Contraseña actual</Label>
                    <Input
                      id="mfa-dis-pw"
                      type="password"
                      autoComplete="current-password"
                      value={disablePw}
                      onChange={(e) => setDisablePw(e.target.value)}
                    />
                    <Label htmlFor="mfa-dis-code">Código TOTP</Label>
                    <Input
                      id="mfa-dis-code"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      pattern="[0-9]{6}"
                      maxLength={6}
                      value={disableCode}
                      onChange={(e) =>
                        setDisableCode(
                          e.target.value.replace(/\D/g, "").slice(0, 6),
                        )
                      }
                      className="font-mono tracking-widest"
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => setDisableOpen(false)}
                        disabled={disableBusy}
                      >
                        Cancelar
                      </Button>
                      <Button
                        type="button"
                        onClick={() => void handleDisable()}
                        disabled={
                          disableBusy ||
                          !disablePw ||
                          disableCode.length !== 6
                        }
                      >
                        {disableBusy ? "Desactivando..." : "Confirmar"}
                      </Button>
                    </div>
                  </div>
                ) : null}

                <Separator />

                <div className="flex flex-col gap-3">
                  <h3 className="text-sm font-semibold">
                    Códigos de recuperación
                  </h3>
                  <p className="text-xs text-[rgb(var(--foreground-muted))]">
                    Regenerar invalida los códigos anteriores y entrega 10
                    nuevos. Pierdes acceso si no los guardas.
                  </p>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Input
                      type="password"
                      placeholder="Contraseña actual"
                      autoComplete="current-password"
                      value={regenPw}
                      onChange={(e) => setRegenPw(e.target.value)}
                    />
                    <Button
                      type="button"
                      onClick={() => void handleRegenerate()}
                      disabled={regenBusy || !regenPw}
                    >
                      {regenBusy ? "Regenerando..." : "Regenerar códigos"}
                    </Button>
                  </div>
                  {regenCodes ? (
                    <ul
                      aria-label="Nuevos códigos de recuperación"
                      className="grid grid-cols-2 gap-2 rounded border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-3 font-mono text-xs"
                    >
                      {regenCodes.map((c) => (
                        <li key={c} className="select-all">
                          {c}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <MfaSetupDialog
        open={mfaDialogOpen}
        onOpenChange={setMfaDialogOpen}
        onCompleted={onMfaChanged}
      />
    </div>
  );
}
