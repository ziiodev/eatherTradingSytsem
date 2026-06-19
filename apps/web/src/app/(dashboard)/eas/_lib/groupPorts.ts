/**
 * Pure edge-classification + synthetic-port derivation for collapsed groups.
 *
 * Given a group's member node-id set, every edge in the canonical graph falls
 * into exactly one bucket relative to that group:
 * - INTERNAL: both ends are members -> hidden while the group is collapsed.
 * - INBOUND:  only the target is a member -> a real node points INTO the group.
 * - OUTBOUND: only the source is a member -> a member points OUT of the group.
 * - EXTERNAL: neither end is a member -> untouched by this group.
 *
 * Inbound/outbound edges become "boundary" edges: while collapsed they are
 * rerouted to deterministic synthetic ports on the single group-node. Ids are
 * derived purely from stable inputs (member id + original handle + edge id) so
 * the same canonical graph always yields the same render graph (idempotent).
 *
 * Nothing here mutates its inputs.
 */
import type { FlowEdge } from "../_types/graph";

/** Where an edge sits relative to a given group. */
export type EdgeRelation = "internal" | "inbound" | "outbound" | "external";

/** A synthetic handle on the collapsed group-node that a proxy edge attaches to. */
export interface GroupPort {
  /** Deterministic handle id, unique within the group-node. */
  id: string;
  /** "target" for inbound ports, "source" for outbound ports. */
  side: "target" | "source";
  /** The member node id this port stands in for. */
  memberId: string;
  /** The original handle id on the member (null when the edge had none). */
  handle: string | null;
}

/** A boundary edge rerouted to point at the group-node instead of a member. */
export interface ProxyEdge {
  edge: FlowEdge;
  port: GroupPort;
}

/** Full classification of the graph's edges for one group. */
export interface GroupEdgeClassification {
  inbound: ProxyEdge[];
  outbound: ProxyEdge[];
  /** Ids of internal edges (hidden while collapsed). */
  internalEdgeIds: Set<string>;
  /** Distinct synthetic ports the group-node must render. */
  ports: GroupPort[];
}

const portIdFor = (
  side: "in" | "out",
  memberId: string,
  handle: string | null,
): string => `${side}_${memberId}_${handle ?? "default"}`;

/** Deterministic id for the proxy that replaces an original boundary edge. */
export const proxyEdgeId = (originalEdgeId: string): string =>
  `proxy_${originalEdgeId}`;

/** Classify a single edge relative to the group's member set. */
export function classifyEdge(edge: FlowEdge, members: Set<string>): EdgeRelation {
  const srcIn = members.has(edge.source);
  const tgtIn = members.has(edge.target);
  if (srcIn && tgtIn) return "internal";
  if (tgtIn) return "inbound";
  if (srcIn) return "outbound";
  return "external";
}

/**
 * Classify every edge for one group and derive its synthetic ports + proxy
 * edges. Pure: returns fresh objects, never mutating `edges` or its members.
 */
export function classifyGroupEdges(
  groupNodeId: string,
  memberIds: Iterable<string>,
  edges: FlowEdge[],
): GroupEdgeClassification {
  const members = new Set(memberIds);
  const inbound: ProxyEdge[] = [];
  const outbound: ProxyEdge[] = [];
  const internalEdgeIds = new Set<string>();
  const portsById = new Map<string, GroupPort>();

  for (const edge of edges) {
    const relation = classifyEdge(edge, members);
    if (relation === "external") continue;
    if (relation === "internal") {
      internalEdgeIds.add(edge.id);
      continue;
    }

    const inboundEdge = relation === "inbound";
    const memberId = inboundEdge ? edge.target : edge.source;
    const handle = (inboundEdge ? edge.targetHandle : edge.sourceHandle) ?? null;
    const portId = portIdFor(inboundEdge ? "in" : "out", memberId, handle);
    const port: GroupPort = portsById.get(portId) ?? {
      id: portId,
      side: inboundEdge ? "target" : "source",
      memberId,
      handle,
    };
    portsById.set(portId, port);

    // Reroute the boundary end onto the group-node's synthetic port.
    const proxied: FlowEdge = inboundEdge
      ? { ...edge, id: proxyEdgeId(edge.id), target: groupNodeId, targetHandle: portId }
      : { ...edge, id: proxyEdgeId(edge.id), source: groupNodeId, sourceHandle: portId };

    (inboundEdge ? inbound : outbound).push({ edge: proxied, port });
  }

  return {
    inbound,
    outbound,
    internalEdgeIds,
    ports: Array.from(portsById.values()),
  };
}
