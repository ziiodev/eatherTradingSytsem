/**
 * Operativa — top-level tab placeholder.
 *
 * The real Operativa surface (DB+MCP hybrid orders, account summary, live
 * WebSocket updates) lands in the sibling `project-operativa` SDD change.
 * Here we only reserve the URL + render a friendly "próximamente" card so
 * the new three-tab layout has a real route to navigate to.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function OperativaPage(): React.JSX.Element {
  return (
    <Card data-testid="operativa-placeholder">
      <CardHeader>
        <CardTitle>Operativa</CardTitle>
        <CardDescription>
          Vista operativa del proyecto — próximamente.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Esta pestaña incluirá el resumen de cuenta, posiciones abiertas,
          aprobaciones pendientes y órdenes recientes en tiempo real. Llegará
          en una próxima entrega.
        </p>
      </CardContent>
    </Card>
  );
}
