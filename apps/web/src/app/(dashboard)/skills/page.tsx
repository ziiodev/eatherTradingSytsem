"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  SKILL_RUNTIME_LABEL,
  SKILL_TYPES,
  SKILL_TYPE_LABEL,
  SKILL_TYPE_DESCRIPTION,
  listSkills,
  type SkillSummary,
  type SkillType,
} from "@/lib/skills";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

/**
 * Skills catalog (v1, storage + display only).
 *
 * Grid grouped by ``type``. Each card surfaces an amber "No ejecutable"
 * badge with a tooltip that points at the future sandbox change — skills
 * cannot be run from here today; this surface is for cataloguing and
 * inspection until ``agent-execution-sandbox`` lands.
 */
export default function SkillsPage(): React.JSX.Element {
  const [items, setItems] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await listSkills();
        if (cancelled) return;
        setItems(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? `Error al cargar (${err.status})`
            : "Error de red";
        setError(msg);
        toast.error(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [refreshNonce]);

  function refresh(): void {
    setLoading(true);
    setRefreshNonce((n) => n + 1);
  }

  const grouped = useMemo(() => {
    const buckets: Record<SkillType, SkillSummary[]> = {
      indicator: [],
      data_source: [],
      analytic: [],
      executor: [],
      risk: [],
    };
    for (const row of items) {
      buckets[row.type].push(row);
    }
    return buckets;
  }, [items]);

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Skills</h1>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Catálogo de trading skills reutilizables que tus agentes podrán
            invocar (almacenamiento; la ejecución llegará con el sandbox).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={loading}
            aria-label="Refrescar listado"
          >
            <RefreshCw className="h-4 w-4" /> Refrescar
          </Button>
          <Link
            href="/skills/new"
            data-testid="new-skill-link"
            className="inline-flex h-8 items-center gap-2 rounded-md bg-[rgb(var(--accent))] px-3 text-xs font-medium text-[rgb(var(--accent-foreground))] hover:bg-[rgb(var(--accent)/0.9)]"
          >
            <Plus className="h-4 w-4" /> Nueva skill
          </Link>
        </div>
      </header>

      {error && (
        <p role="alert" className="text-sm text-[rgb(var(--danger))]">
          {error}
        </p>
      )}

      {loading && items.length === 0 ? (
        <p className="text-sm text-[rgb(var(--foreground-muted))]">Cargando…</p>
      ) : (
        <div className="flex flex-col gap-6">
          {SKILL_TYPES.map((kind) => (
            <section key={kind} className="flex flex-col gap-2">
              <header className="flex items-end justify-between">
                <div>
                  <h2 className="text-lg font-semibold tracking-tight">
                    {SKILL_TYPE_LABEL[kind]}
                  </h2>
                  <p className="text-xs text-[rgb(var(--foreground-muted))]">
                    {SKILL_TYPE_DESCRIPTION[kind]}
                  </p>
                </div>
                <span className="text-xs text-[rgb(var(--foreground-muted))]">
                  {grouped[kind].length} skill
                  {grouped[kind].length === 1 ? "" : "s"}
                </span>
              </header>
              {grouped[kind].length === 0 ? (
                <p className="text-xs text-[rgb(var(--foreground-muted))]">
                  Aún no hay skills de tipo {SKILL_TYPE_LABEL[kind].toLowerCase()}.
                </p>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {grouped[kind].map((row) => (
                    <SkillCard key={row.id} row={row} />
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

interface SkillCardProps {
  row: SkillSummary;
}

function SkillCard({ row }: SkillCardProps): React.JSX.Element {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-base">
            <Link href={`/skills/${row.id}`} className="hover:underline">
              {row.name}
            </Link>
          </CardTitle>
          <Badge variant="accent">v{row.version}</Badge>
        </div>
        <CardDescription className="flex flex-wrap items-center gap-2">
          <Badge variant="muted">{SKILL_TYPE_LABEL[row.type]}</Badge>
          <Badge variant="muted" data-testid="skill-runtime-badge">
            {SKILL_RUNTIME_LABEL[row.runtime]}
          </Badge>
          {row.is_active ? (
            <Badge variant="success">Activa</Badge>
          ) : (
            <Badge variant="muted">Archivada</Badge>
          )}
          {row.used_by_agent_count > 0 && (
            <Badge variant="accent" data-testid="skill-usage-badge">
              {row.used_by_agent_count} agente
              {row.used_by_agent_count === 1 ? "" : "s"}
            </Badge>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-center justify-between gap-2 text-xs text-[rgb(var(--foreground-muted))]">
        {row.runtime === "python" ? (
          <span
            className="inline-flex items-center gap-1 rounded-md border border-[rgb(var(--warning)/0.4)] bg-[rgb(var(--warning)/0.1)] px-2 py-0.5 text-[rgb(var(--warning))]"
            title="Activable cuando arranque el sandbox"
            aria-label="No ejecutable — activable cuando arranque el sandbox"
            data-testid="non-executable-badge"
          >
            No ejecutable
          </span>
        ) : (
          <span aria-hidden />
        )}
        <span>{row.updated_at ? new Date(row.updated_at).toLocaleString() : "—"}</span>
      </CardContent>
    </Card>
  );
}
