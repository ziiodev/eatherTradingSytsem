"use client";

/**
 * /proyectos/[id]/operativa panel — read-only live MT5 view.
 *
 * Sections:
 *   - Account summary (balance / equity / margin / free margin).
 *   - Open positions table (read-only — operator does not close from here).
 *   - Pending approvals (operator can approve/reject).
 *   - Recent orders (paginated, newest-first).
 *
 * Fetching strategy: every section is independent. A network error in
 * one (e.g. MCP unreachable for positions/account) renders an inline
 * error banner but does NOT prevent the others from loading. This matches
 * the backend's per-endpoint failure model.
 */

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  approveApproval,
  getAccount,
  getPositions,
  listApprovals,
  listOrders,
  rejectApproval,
  type Account,
  type Approval,
  type OrderRecord,
  type Position,
} from "@/lib/operativa";
import { Button } from "@/components/ui/button";
import type { ProjectDetail } from "@/lib/projects";

type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; value: T };

const loading = <T,>(): Loadable<T> => ({ state: "loading" });

export function OperativaPanel({
  project,
}: {
  project: ProjectDetail;
}): React.JSX.Element {
  const [account, setAccount] = useState<Loadable<Account>>(loading());
  const [positions, setPositions] = useState<Loadable<Position[]>>(loading());
  const [orders, setOrders] = useState<Loadable<OrderRecord[]>>(loading());
  const [approvals, setApprovals] = useState<Loadable<Approval[]>>(loading());

  const reload = useCallback(async () => {
    setAccount(loading());
    setPositions(loading());
    setOrders(loading());
    setApprovals(loading());
    const id = project.id;
    getAccount(id)
      .then((value) => setAccount({ state: "ready", value }))
      .catch((err) =>
        setAccount({
          state: "error",
          message: err instanceof ApiError ? `Error ${err.status}` : "Error de red",
        }),
      );
    getPositions(id)
      .then((value) => setPositions({ state: "ready", value }))
      .catch((err) =>
        setPositions({
          state: "error",
          message: err instanceof ApiError ? `Error ${err.status}` : "Error de red",
        }),
      );
    listOrders(id)
      .then(({ items }) => setOrders({ state: "ready", value: items }))
      .catch((err) =>
        setOrders({
          state: "error",
          message: err instanceof ApiError ? `Error ${err.status}` : "Error de red",
        }),
      );
    listApprovals(id)
      .then((value) => setApprovals({ state: "ready", value }))
      .catch((err) =>
        setApprovals({
          state: "error",
          message: err instanceof ApiError ? `Error ${err.status}` : "Error de red",
        }),
      );
  }, [project.id]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function decide(approvalId: string, approve: boolean) {
    try {
      if (approve) await approveApproval(project.id, approvalId);
      else await rejectApproval(project.id, approvalId);
      toast.success(approve ? "Aprobado" : "Rechazado");
      void reload();
    } catch (err) {
      toast.error(err instanceof ApiError ? `Error ${err.status}` : "Error de red");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="mb-2 text-lg font-semibold">Cuenta</h2>
        {account.state === "loading" && <p className="text-sm">Cargando…</p>}
        {account.state === "error" && (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {account.message}
          </p>
        )}
        {account.state === "ready" && (
          <dl className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <Field label="Balance" value={`${account.value.balance.toFixed(2)} ${account.value.currency}`} />
            <Field label="Equity" value={`${account.value.equity.toFixed(2)} ${account.value.currency}`} />
            <Field label="Margen" value={`${account.value.margin.toFixed(2)}`} />
            <Field label="Margen libre" value={`${account.value.free_margin.toFixed(2)}`} />
          </dl>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Posiciones abiertas</h2>
        {positions.state === "loading" && <p className="text-sm">Cargando…</p>}
        {positions.state === "error" && (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {positions.message}
          </p>
        )}
        {positions.state === "ready" && positions.value.length === 0 && (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">Sin posiciones abiertas.</p>
        )}
        {positions.state === "ready" && positions.value.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[rgb(var(--foreground-muted))]">
                <th className="py-1">Ticket</th>
                <th>Simbolo</th>
                <th>Lado</th>
                <th>Volumen</th>
                <th>Apertura</th>
                <th>SL</th>
                <th>Profit</th>
              </tr>
            </thead>
            <tbody>
              {positions.value.map((p) => (
                <tr key={p.ticket} className="border-t border-[rgb(var(--border))]">
                  <td className="py-1">{p.ticket}</td>
                  <td>{p.symbol}</td>
                  <td>{p.side}</td>
                  <td>{p.volume}</td>
                  <td>{p.price_open.toFixed(5)}</td>
                  <td>{p.sl?.toFixed(5) ?? "—"}</td>
                  <td>{p.profit.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Aprobaciones pendientes</h2>
        {approvals.state === "loading" && <p className="text-sm">Cargando…</p>}
        {approvals.state === "error" && (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {approvals.message}
          </p>
        )}
        {approvals.state === "ready" && approvals.value.length === 0 && (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">Sin aprobaciones pendientes.</p>
        )}
        {approvals.state === "ready" &&
          approvals.value.map((a) => (
            <div key={a.id} className="mb-2 rounded border border-[rgb(var(--border))] p-2 text-sm">
              <div className="mb-1 font-mono text-xs text-[rgb(var(--foreground-muted))]">{a.id}</div>
              <pre className="overflow-auto text-xs">{JSON.stringify(a.payload, null, 2)}</pre>
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={() => void decide(a.id, true)}>
                  Aprobar
                </Button>
                <Button size="sm" variant="outline" onClick={() => void decide(a.id, false)}>
                  Rechazar
                </Button>
              </div>
            </div>
          ))}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Órdenes recientes</h2>
        {orders.state === "loading" && <p className="text-sm">Cargando…</p>}
        {orders.state === "error" && (
          <p role="alert" className="text-sm text-[rgb(var(--danger))]">
            {orders.message}
          </p>
        )}
        {orders.state === "ready" && orders.value.length === 0 && (
          <p className="text-sm text-[rgb(var(--foreground-muted))]">Sin órdenes registradas.</p>
        )}
        {orders.state === "ready" && orders.value.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-[rgb(var(--foreground-muted))]">
                <th className="py-1">Creada</th>
                <th>Simbolo</th>
                <th>Lado</th>
                <th>Volumen</th>
                <th>SL</th>
                <th>Estado</th>
                <th>Ticket</th>
              </tr>
            </thead>
            <tbody>
              {orders.value.map((o) => (
                <tr key={o.id} className="border-t border-[rgb(var(--border))]">
                  <td className="py-1">{o.created_at?.slice(0, 19) ?? "—"}</td>
                  <td>{o.symbol}</td>
                  <td>{o.side}</td>
                  <td>{o.volume}</td>
                  <td>{o.sl.toFixed(5)}</td>
                  <td>{o.status}</td>
                  <td>{o.mt5_ticket ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-[rgb(var(--foreground-muted))]">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}
