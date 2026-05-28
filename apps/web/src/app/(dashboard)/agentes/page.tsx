"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, RefreshCw, Archive, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  AGENT_TYPES,
  AGENT_TYPE_DESCRIPTION,
  AGENT_TYPE_LABEL,
  AGENT_TYPE_TEMPLATE,
  AGENT_TYPE_DEFAULT_ENTRYPOINT,
  archiveAgent,
  createAgent,
  deleteAgent,
  listAgents,
  type AgentSummary,
  type AgentType,
} from "@/lib/agents";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

type TabKey = AgentType | "all";

const TAB_LABEL: Record<TabKey, string> = {
  worker: "Workers",
  investigator: "Investigators",
  auditor: "Auditors",
  all: "Todos",
};

function formatTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function AgentesPage(): React.JSX.Element {
  const [tab, setTab] = useState<TabKey>("all");
  const [items, setItems] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // ``refreshNonce`` is bumped to force a re-fetch without touching state
  // inside the effect body (lint: react-hooks/set-state-in-effect).
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      try {
        const data = await listAgents();
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

  const filtered = useMemo(() => {
    if (tab === "all") return items;
    return items.filter((row) => row.type === tab);
  }, [items, tab]);

  async function runArchive(agent: AgentSummary): Promise<void> {
    try {
      await archiveAgent(agent.id);
      setItems((prev) =>
        prev.map((row) =>
          row.id === agent.id ? { ...row, is_active: false } : row,
        ),
      );
      toast.success(`"${agent.name}" archivado`);
    } catch {
      toast.error("No se pudo archivar el agente");
    }
  }

  async function runDelete(agent: AgentSummary): Promise<void> {
    if (!confirm(`Eliminar el agente "${agent.name}" definitivamente?`)) return;
    try {
      await deleteAgent(agent.id);
      setItems((prev) => prev.filter((row) => row.id !== agent.id));
      toast.success("Agente eliminado");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error(
          "El agente está referenciado por proyectos; desvincúlalos primero.",
        );
      } else if (err instanceof ApiError && err.status === 404) {
        toast.error("Agente no encontrado");
        refresh();
      } else {
        toast.error("No se pudo eliminar el agente");
      }
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agentes</h1>
          <p className="text-sm text-[rgb(var(--foreground-muted))]">
            Catálogo de agentes reutilizables (Worker / Investigator / Auditor).
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
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> Nuevo agente
          </Button>
        </div>
      </header>

      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as TabKey)}
        className="gap-4"
      >
        <TabsList>
          {(["worker", "investigator", "auditor", "all"] as TabKey[]).map(
            (key) => (
              <TabsTrigger key={key} value={key}>
                {TAB_LABEL[key]}
              </TabsTrigger>
            ),
          )}
        </TabsList>

        <TabsContent value={tab}>
          <Card>
            <CardHeader>
              <CardTitle>{TAB_LABEL[tab]}</CardTitle>
              <CardDescription>
                {filtered.length} agente{filtered.length === 1 ? "" : "s"} en
                esta vista
              </CardDescription>
            </CardHeader>
            <CardContent>
              {error && (
                <p
                  role="alert"
                  className="mb-3 text-sm text-[rgb(var(--danger))]"
                >
                  {error}
                </p>
              )}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nombre</TableHead>
                    <TableHead>Tipo</TableHead>
                    <TableHead>Versión</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Proyectos</TableHead>
                    <TableHead>Actualizado</TableHead>
                    <TableHead className="text-right">Acciones</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {loading && filtered.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className="text-center text-[rgb(var(--foreground-muted))]"
                      >
                        Cargando…
                      </TableCell>
                    </TableRow>
                  )}
                  {!loading && filtered.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className="text-center text-[rgb(var(--foreground-muted))]"
                      >
                        No hay agentes en esta vista.
                      </TableCell>
                    </TableRow>
                  )}
                  {filtered.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className="font-medium">
                        <Link
                          className="hover:underline"
                          href={`/agentes/${row.id}`}
                        >
                          {row.name}
                        </Link>
                      </TableCell>
                      <TableCell>{AGENT_TYPE_LABEL[row.type]}</TableCell>
                      <TableCell>v{row.version}</TableCell>
                      <TableCell>
                        {row.is_active ? (
                          <Badge variant="success">Activo</Badge>
                        ) : (
                          <Badge variant="muted">Archivado</Badge>
                        )}
                      </TableCell>
                      <TableCell>{row.projects_using}</TableCell>
                      <TableCell className="text-xs text-[rgb(var(--foreground-muted))]">
                        {formatTs(row.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex items-center gap-1">
                          {row.is_active && (
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Archivar ${row.name}`}
                              onClick={() => void runArchive(row)}
                            >
                              <Archive className="h-4 w-4" />
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Eliminar ${row.name}`}
                            onClick={() => void runDelete(row)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <CreateAgentDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(createdId) => {
          refresh();
          setCreateOpen(false);
          // Best-effort UX: jump to detail so the user can edit logica.
          // Wrapped in setTimeout so the dialog close animation completes.
          setTimeout(() => {
            window.location.href = `/agentes/${createdId}`;
          }, 0);
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Create dialog — 2-step wizard.
// Step 1: pick a type (loads a template for that type).
// Step 2: fill name/description and tweak entrypoint.
// ---------------------------------------------------------------------------

interface CreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}

function CreateAgentDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateDialogProps): React.JSX.Element {
  // Bump on every open so the inner form remounts with fresh state.
  // Avoids resetting state from inside a useEffect (lint rule).
  const [openId, setOpenId] = useState(0);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (open) setOpenId((n) => n + 1);
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <CreateAgentForm
          key={openId}
          onOpenChange={onOpenChange}
          onCreated={onCreated}
        />
      </DialogContent>
    </Dialog>
  );
}

interface CreateFormProps {
  onOpenChange: (open: boolean) => void;
  onCreated: (id: string) => void;
}

function CreateAgentForm({
  onOpenChange: _onOpenChange,
  onCreated,
}: CreateFormProps): React.JSX.Element {
  const [step, setStep] = useState<1 | 2>(1);
  const [type, setType] = useState<AgentType>("worker");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [entrypoint, setEntrypoint] = useState<string>(
    AGENT_TYPE_DEFAULT_ENTRYPOINT.worker,
  );
  const [submitting, setSubmitting] = useState(false);
  const router = useRouter();

  async function submit(): Promise<void> {
    setSubmitting(true);
    try {
      const detail = await createAgent({
        name: name.trim(),
        type,
        logica: AGENT_TYPE_TEMPLATE[type],
        description: description.trim() || undefined,
        entrypoint: entrypoint.trim() || undefined,
      });
      toast.success(`Agente "${detail.name}" creado`);
      onCreated(detail.id);
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(`Error (${err.status})`);
      } else {
        toast.error("No se pudo crear el agente");
      }
    } finally {
      setSubmitting(false);
    }
    // Avoid unused-router warning — kept for potential future router.push().
    void router;
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {step === 1 ? "Elige el tipo de agente" : `Nuevo ${AGENT_TYPE_LABEL[type]}`}
        </DialogTitle>
        <DialogDescription>
          {step === 1
            ? "Cada tipo trae su plantilla de código y entrypoint por convención."
            : "Después podrás editar el código en la pantalla de detalle."}
        </DialogDescription>
      </DialogHeader>

      {step === 1 ? (
          <div className="grid gap-2">
            {AGENT_TYPES.map((kind) => (
              <button
                key={kind}
                type="button"
                onClick={() => {
                  setType(kind);
                  setEntrypoint(AGENT_TYPE_DEFAULT_ENTRYPOINT[kind]);
                  setStep(2);
                }}
                className="flex flex-col items-start gap-1 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-3 text-left transition-colors hover:border-[rgb(var(--accent))]"
                data-testid={`pick-type-${kind}`}
              >
                <span className="text-sm font-semibold">
                  {AGENT_TYPE_LABEL[kind]}
                </span>
                <span className="text-xs text-[rgb(var(--foreground-muted))]">
                  {AGENT_TYPE_DESCRIPTION[kind]}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="agent-name">Nombre</Label>
              <Input
                id="agent-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ej. EURUSD trend follower"
                maxLength={100}
                required
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="agent-entrypoint">Entrypoint</Label>
              <Input
                id="agent-entrypoint"
                value={entrypoint}
                onChange={(e) => setEntrypoint(e.target.value)}
                placeholder={AGENT_TYPE_DEFAULT_ENTRYPOINT[type]}
                maxLength={120}
              />
              <p className="text-xs text-[rgb(var(--foreground-muted))]">
                Convención para {AGENT_TYPE_LABEL[type]}:{" "}
                <code>{AGENT_TYPE_DEFAULT_ENTRYPOINT[type]}</code>
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="agent-description">Descripción (opcional)</Label>
              <Textarea
                id="agent-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={4000}
                rows={3}
              />
            </div>
          </div>
        )}

        <DialogFooter>
          {step === 2 && (
            <Button
              variant="outline"
              onClick={() => setStep(1)}
              disabled={submitting}
            >
              Atrás
            </Button>
          )}
          <Button
            onClick={() => {
              if (step === 1) return; // step 1 advances on type click
              void submit();
            }}
            disabled={step === 1 || submitting || !name.trim()}
          >
            {submitting ? "Creando…" : "Crear agente"}
          </Button>
        </DialogFooter>
    </>
  );
}
