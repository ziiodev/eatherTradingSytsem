"use client";

import { useCallback, useEffect, useState } from "react";

import { ProfileTab } from "@/app/(dashboard)/configuracion/_components/profile-tab";
import { SecurityTab } from "@/app/(dashboard)/configuracion/_components/security-tab";
import { SessionsTab } from "@/app/(dashboard)/configuracion/_components/sessions-tab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiGet } from "@/lib/api";
import type { Me } from "@/lib/me";

/**
 * Settings / Configuracion — Perfil / Seguridad / Sesiones.
 *
 * The page loads the caller's profile once and threads it down to each
 * tab. Each tab owns its own state for its own mutations; lifting the
 * `Me` object up here keeps the verified-email banner consistent across
 * tabs without re-fetching on every tab switch.
 */
export default function ConfiguracionPage(): React.JSX.Element {
  const [tab, setTab] = useState<"perfil" | "seguridad" | "sesiones">(
    "perfil",
  );
  const [me, setMe] = useState<Me | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadMe = useCallback(async () => {
    try {
      const data = await apiGet<Me>("/api/auth/me");
      setMe(data);
      setLoadError(null);
    } catch (err) {
      if (err instanceof ApiError) {
        setLoadError(`No se pudo cargar el perfil (HTTP ${err.status}).`);
      } else {
        setLoadError("No se pudo cargar el perfil.");
      }
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadMe();
  }, [loadMe]);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Configuración
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Gestiona tu perfil, credenciales y sesiones activas.
        </p>
      </header>

      {loadError ? (
        <div
          role="alert"
          className="rounded-md border border-[rgb(var(--danger))] bg-[rgb(var(--danger)/0.1)] px-4 py-3 text-sm text-[rgb(var(--danger))]"
        >
          {loadError}
        </div>
      ) : null}

      {me && me.email_verified_at === null ? (
        <div
          role="status"
          className="rounded-md border border-[rgb(var(--warning,var(--accent)))] bg-[rgb(var(--background-elevated))] px-4 py-3 text-sm text-[rgb(var(--foreground))]"
        >
          Cuenta pendiente de verificación. Revisa tu correo electrónico
          para completar la verificación.
        </div>
      ) : null}

      <Tabs
        value={tab}
        onValueChange={(v) =>
          setTab(v as "perfil" | "seguridad" | "sesiones")
        }
      >
        <TabsList>
          <TabsTrigger value="perfil">Perfil</TabsTrigger>
          <TabsTrigger value="seguridad">Seguridad</TabsTrigger>
          <TabsTrigger value="sesiones">Sesiones</TabsTrigger>
        </TabsList>

        <TabsContent value="perfil">
          <ProfileTab me={me} onUpdated={(next) => setMe(next)} />
        </TabsContent>
        <TabsContent value="seguridad">
          <SecurityTab
            me={me}
            onEmailChanged={(next) => setMe(next)}
            onMfaChanged={() => {
              void loadMe();
            }}
          />
        </TabsContent>
        <TabsContent value="sesiones">
          <SessionsTab />
        </TabsContent>
      </Tabs>
    </section>
  );
}
