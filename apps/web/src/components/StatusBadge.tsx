import { Badge } from "@/components/ui/badge";
import { PAIR_STATUS_LABEL, type PairStatus } from "@/lib/pairs";

const STATUS_VARIANT: Record<
  PairStatus,
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
  status: PairStatus;
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
      {PAIR_STATUS_LABEL[status]}
    </Badge>
  );
}
