"use client";

/**
 * Memoria — episodic + semantic memory inspector.
 *
 * Two tabs:
 *  - "Episódica": timeline of (s, a, r, s') episodes within a date window.
 *  - "Semántica": grouped list of active semantic rules.
 *
 * Writes never happen here — episodic rows land via the sandboxed Worker
 * ctx, and semantic rules are promoted by the deep-sleep orchestrator.
 */

import { use, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  fetchEpisodicMemory,
  fetchSemanticMemory,
  type EpisodicMemory,
  type SemanticMemory,
} from "@/lib/sleep";
import { Badge } from "@/components/ui/badge";
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
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const DEFAULT_WINDOW_DAYS = 7;
const PAGE_SIZE = 50;

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function MemoriaPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): React.JSX.Element {
  const { id: projectId } = use(params);
  const [tab, setTab] = useState<"episodica" | "semantica">("episodica");

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-2xl font-semibold tracking-tight">Memoria</h2>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="episodica" data-testid="memoria-tab-episodica">
            Episódica
          </TabsTrigger>
          <TabsTrigger value="semantica" data-testid="memoria-tab-semantica">
            Semántica
          </TabsTrigger>
        </TabsList>

        <TabsContent value="episodica">
          <EpisodicTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="semantica">
          <SemanticTab projectId={projectId} />
        </TabsContent>
      </Tabs>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Episodic tab
// ---------------------------------------------------------------------------
function EpisodicTab({
  projectId,
}: {
  projectId: string;
}): React.JSX.Element {
  const [since, setSince] = useState<string>(isoDaysAgo(DEFAULT_WINDOW_DAYS));
  const [until, setUntil] = useState<string>(todayIso());
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<EpisodicMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const data = await fetchEpisodicMemory(projectId, {
        since: `${since}T00:00:00Z`,
        until: `${until}T23:59:59Z`,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setItems(data.items);
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `Error (${err.status})`
          : "Error de red",
      );
    } finally {
      setLoading(false);
    }
  }, [projectId, since, until, page]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Línea temporal de episodios</CardTitle>
        <CardDescription>
          Cada fila es una transición (s, a, r, s′). Por defecto se muestran
          los últimos {DEFAULT_WINDOW_DAYS} días.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="since">Desde</Label>
            <Input
              id="since"
              type="date"
              value={since}
              onChange={(e) => {
                setSince(e.target.value);
                setPage(0);
              }}
              data-testid="episodica-since"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="until">Hasta</Label>
            <Input
              id="until"
              type="date"
              value={until}
              onChange={(e) => {
                setUntil(e.target.value);
                setPage(0);
              }}
              data-testid="episodica-until"
            />
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setSince(isoDaysAgo(DEFAULT_WINDOW_DAYS));
              setUntil(todayIso());
              setPage(0);
            }}
          >
            Reset
          </Button>
        </div>

        {loading ? (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Cargando…
          </p>
        ) : error ? (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {error}
          </p>
        ) : items.length === 0 ? (
          <p
            className="text-sm text-[rgb(var(--foreground-muted))]"
            data-testid="episodica-empty"
          >
            Sin episodios en la ventana seleccionada.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cuando</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Acción</TableHead>
                <TableHead>Reward</TableHead>
                <TableHead>Tag</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((ep) => (
                <TableRow key={ep.id} data-testid={`episodica-row-${ep.id}`}>
                  <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                    {formatDate(ep.created_at)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {shortHash(ep.state_key)}
                  </TableCell>
                  <TableCell className="text-xs">{ep.action}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatReward(ep.reward)}
                  </TableCell>
                  <TableCell>
                    {isSpecial(ep) ? (
                      <Badge variant="warning">special</Badge>
                    ) : (
                      <Badge variant="muted">normal</Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-[rgb(var(--foreground-muted))]">
            Página {page + 1}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Anterior
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={items.length < PAGE_SIZE}
              onClick={() => setPage((p) => p + 1)}
            >
              Siguiente
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Semantic tab
// ---------------------------------------------------------------------------
function SemanticTab({
  projectId,
}: {
  projectId: string;
}): React.JSX.Element {
  const [rules, setRules] = useState<SemanticMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await fetchSemanticMemory(projectId, { active: true });
        if (cancelled) return;
        setRules(data.items);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? `Error (${err.status})`
            : "Error de red",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const grouped = useMemo(() => groupByRuleType(rules), [rules]);

  if (loading) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-[rgb(var(--foreground-muted))]">
          Cargando…
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-[rgb(var(--danger))]" role="alert">
          {error}
        </CardContent>
      </Card>
    );
  }
  if (rules.length === 0) {
    return (
      <Card>
        <CardContent
          className="p-6 text-sm text-[rgb(var(--foreground-muted))]"
          data-testid="semantica-empty"
        >
          Sin reglas semánticas activas. Aparecerán cuando el sueño profundo
          promueva su primera regla.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {grouped.map(({ ruleType, items }) => (
        <Card key={ruleType} data-testid={`semantica-group-${ruleType}`}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Badge variant="accent">{ruleType}</Badge>
              <span className="text-base font-medium">
                {items.length} regla{items.length === 1 ? "" : "s"}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {items.map((rule) => (
              <RuleRow key={rule.id} rule={rule} />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function RuleRow({ rule }: { rule: SemanticMemory }): React.JSX.Element {
  const confidence = readConfidence(rule.payload);
  const source = readSource(rule.payload);
  return (
    <div
      data-testid={`semantica-rule-${rule.id}`}
      className="flex flex-col gap-1 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background))] p-3"
    >
      <p className="text-sm">{rule.body}</p>
      <div className="flex flex-wrap gap-2 text-xs">
        {confidence !== null ? (
          <Badge variant="muted">confianza {confidence.toFixed(2)}</Badge>
        ) : null}
        {source ? <Badge variant="muted">fuente: {source}</Badge> : null}
        <Badge variant="muted">{formatDate(rule.created_at)}</Badge>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function groupByRuleType(
  rules: SemanticMemory[],
): Array<{ ruleType: string; items: SemanticMemory[] }> {
  const map = new Map<string, SemanticMemory[]>();
  for (const r of rules) {
    const list = map.get(r.rule_type) ?? [];
    list.push(r);
    map.set(r.rule_type, list);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([ruleType, items]) => ({ ruleType, items }));
}

function readConfidence(payload: Record<string, unknown>): number | null {
  const c = payload?.confidence;
  if (typeof c === "number" && Number.isFinite(c)) return c;
  if (typeof c === "string" && Number.isFinite(Number(c))) return Number(c);
  return null;
}

function readSource(payload: Record<string, unknown>): string | null {
  const s = payload?.source;
  return typeof s === "string" ? s : null;
}

function shortHash(stateKey: string): string {
  return stateKey.length <= 12 ? stateKey : `${stateKey.slice(0, 12)}…`;
}

function isSpecial(ep: EpisodicMemory): boolean {
  const meta = ep.meta_data ?? {};
  return Boolean((meta as Record<string, unknown>).is_special);
}

function formatReward(reward: string | number): string {
  const n = typeof reward === "number" ? reward : Number(reward);
  if (!Number.isFinite(n)) return String(reward);
  return `${(n * 100).toFixed(2)}%`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(`${iso}Z`);
    return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  } catch {
    return iso;
  }
}

