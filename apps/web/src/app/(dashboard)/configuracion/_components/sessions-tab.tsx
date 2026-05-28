"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import {
  listSessions,
  revokeOtherSessions,
  revokeSession,
  type SessionItem,
} from "@/lib/me";

/**
 * Sesiones tab — list + revoke.
 *
 * Pagination is keyset (opaque cursor) — we render a "Cargar más"
 * button when the API returns a next_cursor. The current session is
 * highlighted and the "Revocar" button on it is disabled — the
 * canonical way to end the current session is the sidebar's logout
 * action, mirroring the backend's ``use_logout_instead`` 400.
 */

function formatRelative(timestamp: string): string {
  try {
    const date = new Date(timestamp);
    return date.toLocaleString("es-ES", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return timestamp;
  }
}

export function SessionsTab(): React.JSX.Element {
  const [items, setItems] = useState<SessionItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [hasError, setHasError] = useState<boolean>(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [revokingOthers, setRevokingOthers] = useState<boolean>(false);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setHasError(false);
    try {
      const page = await listSessions({ limit: 20 });
      setItems(page.items);
      setCursor(page.next_cursor);
    } catch {
      setHasError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoading(true);
    try {
      const page = await listSessions({ limit: 20, cursor });
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch {
      toast.error("No se pudo cargar más sesiones.");
    } finally {
      setLoading(false);
    }
  }, [cursor]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadFirstPage();
  }, [loadFirstPage]);

  const handleRevoke = async (sessionId: string): Promise<void> => {
    setBusyId(sessionId);
    try {
      await revokeSession(sessionId);
      setItems((prev) =>
        prev.map((s) =>
          s.id === sessionId
            ? { ...s, revoked_at: new Date().toISOString() }
            : s,
        ),
      );
      toast.success("Sesión revocada.");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) {
          toast.error(
            "No puedes revocar tu sesión actual; usa Cerrar sesión.",
          );
        } else if (err.status === 404) {
          toast.error("Sesión no encontrada.");
        } else {
          toast.error("No se pudo revocar la sesión.");
        }
      } else {
        toast.error("No se pudo revocar la sesión.");
      }
    } finally {
      setBusyId(null);
    }
  };

  const handleRevokeOthers = async (): Promise<void> => {
    setRevokingOthers(true);
    try {
      const result = await revokeOtherSessions();
      toast.success(`Se revocaron ${result.revoked} sesiones.`);
      await loadFirstPage();
    } catch {
      toast.error("No se pudieron revocar las otras sesiones.");
    } finally {
      setRevokingOthers(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Sesiones activas</CardTitle>
          <CardDescription>
            Cada sesión representa un dispositivo donde has iniciado sesión.
          </CardDescription>
        </div>
        <Button
          variant="destructive"
          onClick={() => {
            void handleRevokeOthers();
          }}
          disabled={revokingOthers || items.length <= 1}
        >
          {revokingOthers
            ? "Revocando..."
            : "Cerrar otras sesiones"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {hasError ? (
          <div
            role="alert"
            className="rounded-md border border-[rgb(var(--danger))] bg-[rgb(var(--danger)/0.1)] px-3 py-2 text-sm text-[rgb(var(--danger))]"
          >
            No se pudieron cargar las sesiones.
          </div>
        ) : null}

        {loading && items.length === 0 ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Cargando sesiones...
          </p>
        ) : null}

        {!loading && items.length === 0 && !hasError ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            No hay sesiones activas.
          </p>
        ) : null}

        <ul className="flex flex-col gap-2">
          {items.map((session) => (
            <li
              key={session.id}
              className={
                "flex flex-col gap-1 rounded-md border px-3 py-2 text-sm " +
                (session.is_current
                  ? "border-[rgb(var(--accent))] bg-[rgb(var(--accent)/0.05)]"
                  : "border-[rgb(var(--border))]")
              }
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex flex-col">
                  <span className="font-medium text-[rgb(var(--foreground))]">
                    {session.user_agent ?? "Dispositivo desconocido"}
                  </span>
                  <span className="text-xs text-[rgb(var(--foreground-muted))]">
                    IP: {session.ip_address ?? "—"} · Iniciada{" "}
                    {formatRelative(session.issued_at)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {session.is_current ? (
                    <span className="rounded-md bg-[rgb(var(--accent))] px-2 py-0.5 text-xs font-medium text-[rgb(var(--accent-foreground))]">
                      Actual
                    </span>
                  ) : null}
                  {session.revoked_at ? (
                    <span className="rounded-md border border-[rgb(var(--border))] px-2 py-0.5 text-xs text-[rgb(var(--foreground-muted))]">
                      Revocada
                    </span>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        void handleRevoke(session.id);
                      }}
                      disabled={
                        session.is_current ||
                        busyId === session.id
                      }
                      title={
                        session.is_current
                          ? "Usa Cerrar sesión para terminar esta sesión"
                          : undefined
                      }
                    >
                      {busyId === session.id ? "Revocando..." : "Revocar"}
                    </Button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>

        {cursor ? (
          <div>
            <Button
              variant="outline"
              onClick={() => {
                void loadMore();
              }}
              disabled={loading}
            >
              {loading ? "Cargando..." : "Cargar más"}
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
