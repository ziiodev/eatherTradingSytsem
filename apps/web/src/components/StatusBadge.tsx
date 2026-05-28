import { Badge } from "@/components/ui/badge";
import { PROJECT_STATUS_LABEL, type ProjectStatus } from "@/lib/projects";

const STATUS_VARIANT: Record<
  ProjectStatus,
  "success" | "warning" | "danger" | "muted" | "accent"
> = {
  active: "success",
  paused: "warning",
  stopped: "muted",
  error: "danger",
  maintenance: "accent",
  inactive: "muted",
};

export interface StatusBadgeProps {
  status: ProjectStatus;
  className?: string;
}

/**
 * Single source of truth for "status → colour" mapping in the UI.
 * Centralising it here keeps every list, detail and dropdown consistent.
 */
export function StatusBadge({
  status,
  className,
}: StatusBadgeProps): React.JSX.Element {
  return (
    <Badge variant={STATUS_VARIANT[status]} className={className}>
      {PROJECT_STATUS_LABEL[status]}
    </Badge>
  );
}
