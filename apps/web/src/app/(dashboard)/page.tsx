import { cookies } from "next/headers";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Project {
  id: string;
  name: string;
  symbol?: string | null;
  status: string;
}

/**
 * Dashboard main view — shows ONLY active projects (`status = 'active'`),
 * per the Charter "Dashboard & UI" section. Other statuses live under the
 * Proyectos sidebar entry.
 */
async function fetchActiveProjects(
  cookieHeader: string,
): Promise<Project[] | null> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${apiBase}/api/projects?status=active`, {
      headers: { Cookie: cookieHeader, Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Project[] | { items?: Project[] };
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
  const projects = await fetchActiveProjects(cookieHeader);

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Proyectos activos
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Vista general de los proyectos en estado <code>active</code>. Los
          proyectos pausados, detenidos, con error o en mantenimiento se
          gestionan desde la sección <strong>Proyectos</strong>.
        </p>
      </header>

      {projects === null && (
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

      {projects !== null && projects.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Aún no tienes proyectos activos</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-[rgb(var(--foreground-muted))]">
              No tienes proyectos activos aún. Crea uno en Proyectos.
            </p>
          </CardContent>
        </Card>
      )}

      {projects !== null && projects.length > 0 && (
        <ul className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => (
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
