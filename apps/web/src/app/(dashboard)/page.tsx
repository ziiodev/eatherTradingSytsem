import { cookies } from "next/headers";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Pair {
  id: string;
  name: string;
  symbol?: string | null;
  status: string;
}

/**
 * Dashboard main view — shows ONLY active pairs (`status = 'active'`),
 * per the Charter "Dashboard & UI" section. Other statuses live under the
 * Cuentas sidebar entry (each pair sits under its owning account).
 */
async function fetchActivePairs(
  cookieHeader: string,
): Promise<Pair[] | null> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${apiBase}/api/pairs?status=active`, {
      headers: { Cookie: cookieHeader, Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Pair[] | { items?: Pair[] };
    return Array.isArray(body) ? body : (body.items ?? []);
  } catch {
    return null;
  }
}

export default async function DashboardHome(): Promise<React.JSX.Element> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
  const pairs = await fetchActivePairs(cookieHeader);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Pares activos
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Vista general de los pares en estado <code>active</code>. Los
          pares pausados, detenidos, con error o en mantenimiento se
          gestionan desde la sección <strong>Cuentas</strong>.
        </p>
      </header>

      {pairs === null && (
        <Card>
          <CardHeader>
            <CardTitle>Backend no disponible</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              No se pudo contactar al backend. Verifica que la API esté en
              ejecución en <code>http://localhost:8000</code>.
            </p>
          </CardContent>
        </Card>
      )}

      {pairs !== null && pairs.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Aún no tienes pares activos</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              No tienes pares activos aún. Crea uno desde Cuentas.
            </p>
          </CardContent>
        </Card>
      )}

      {pairs !== null && pairs.length > 0 && (
        <ul className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {pairs.map((p) => (
            <li key={p.id}>
              <Card>
                <CardHeader>
                  <CardTitle>{p.name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-[rgb(var(--foreground-muted))]">
                    {p.symbol ?? "Sin símbolo"} · estado: {p.status}
                  </p>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
