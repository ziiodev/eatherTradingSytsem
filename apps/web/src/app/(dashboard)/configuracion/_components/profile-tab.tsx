"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

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
import { ApiError } from "@/lib/api";
import { patchMe, type Me } from "@/lib/me";

/**
 * Perfil tab — display_name + avatar_url.
 *
 * The zod schema mirrors the backend Pydantic constraints in
 * ``routers/me.py`` exactly (1..100 / http(s) only / 2048-char cap).
 * Keeping the rules in sync on both sides is intentional — server is
 * the authority, client is the friendly UX.
 */

const profileSchema = z.object({
  display_name: z
    .string()
    .min(1, "Nombre obligatorio.")
    .max(100, "Máximo 100 caracteres.")
    .or(z.literal("").transform(() => "")),
  avatar_url: z
    .string()
    .max(2048, "Máximo 2048 caracteres.")
    .refine(
      (v) =>
        v === "" ||
        /^https?:\/\/[^\s]+$/i.test(v),
      "Debe ser una URL http(s).",
    ),
});

type ProfileFormValues = z.infer<typeof profileSchema>;

interface ProfileTabProps {
  me: Me | null;
  onUpdated: (next: Me) => void;
}

export function ProfileTab({
  me,
  onUpdated,
}: ProfileTabProps): React.JSX.Element {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting, isDirty },
    reset,
  } = useForm<ProfileFormValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      display_name: me?.display_name ?? "",
      avatar_url: me?.avatar_url ?? "",
    },
  });

  useEffect(() => {
    if (me) {
      reset({
        display_name: me.display_name ?? "",
        avatar_url: me.avatar_url ?? "",
      });
    }
  }, [me, reset]);

  const onSubmit = async (values: ProfileFormValues): Promise<void> => {
    try {
      const updated = await patchMe({
        display_name: values.display_name || null,
        avatar_url: values.avatar_url || null,
      });
      onUpdated(updated);
      reset({
        display_name: updated.display_name ?? "",
        avatar_url: updated.avatar_url ?? "",
      });
      toast.success("Perfil actualizado.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        toast.error("Datos inválidos.");
      } else {
        toast.error("No se pudo actualizar el perfil.");
      }
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Perfil</CardTitle>
        <CardDescription>
          Tu nombre visible y avatar son los únicos datos públicos del perfil.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            void handleSubmit(onSubmit)(e);
          }}
          noValidate
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              value={me?.email ?? ""}
              readOnly
              disabled
            />
            <p className="text-xs text-[rgb(var(--foreground-muted))]">
              Cambia tu correo desde la pestaña Seguridad.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="display_name">Nombre visible</Label>
            <Input
              id="display_name"
              type="text"
              autoComplete="nickname"
              {...register("display_name")}
              aria-invalid={errors.display_name ? "true" : "false"}
            />
            {errors.display_name ? (
              <p className="text-xs text-[rgb(var(--danger))]">
                {errors.display_name.message}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="avatar_url">Avatar (URL)</Label>
            <Input
              id="avatar_url"
              type="url"
              placeholder="https://..."
              {...register("avatar_url")}
              aria-invalid={errors.avatar_url ? "true" : "false"}
            />
            {errors.avatar_url ? (
              <p className="text-xs text-[rgb(var(--danger))]">
                {errors.avatar_url.message}
              </p>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <Button type="submit" disabled={isSubmitting || !isDirty}>
              {isSubmitting ? "Guardando..." : "Guardar cambios"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
