/**
 * Chat — top-level tab placeholder.
 *
 * The real Chat surface (Claude-Code-like assistant per project, with
 * conversation persistence, audit log y aprobación de acciones del agente)
 * llegará en el sibling SDD `project-chat`. Aquí sólo reservamos la URL.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function ChatPage(): React.JSX.Element {
  return (
    <Card data-testid="chat-placeholder">
      <CardHeader>
        <CardTitle>Chat</CardTitle>
        <CardDescription>
          Asistente conversacional del proyecto — próximamente.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Esta pestaña permitirá conversar con el agente del proyecto,
          revisar el historial de la sesión y aprobar acciones sensibles
          antes de que se ejecuten. Llegará en una próxima entrega.
        </p>
      </CardContent>
    </Card>
  );
}
