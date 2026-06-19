/**
 * Pure helpers for the render-only group overlay (`groups: GroupMeta[]`).
 *
 * These functions never mutate their inputs and never touch `nodes[]`/`edges[]`,
 * so they are trivially unit-testable and safe to call inside Zustand `set`
 * reducers. The graphStore wires them into undoable steps; all invariant
 * enforcement (membership uniqueness, >=2 members, name required, pruning on
 * node removal) lives here so the store stays thin.
 */
import type { GroupMeta } from "../_types/graph";

/** Minimum members a group must retain to remain valid. */
export const MIN_GROUP_MEMBERS = 2;

/** Generate a reasonably-unique group id without a uuid dependency. */
export const newGroupId = (): string =>
  `g_${Math.random().toString(36).slice(2, 10)}`;

/**
 * The result of creating a group: the next `groups[]` array and the id of the
 * newly created group (or `null` if creation was rejected).
 */
export interface AddGroupResult {
  groups: GroupMeta[];
  id: string | null;
}

/**
 * Append a new group from `nodeIds` with a trimmed `name`. Enforces:
 * - name must be non-empty after trimming;
 * - at least {@link MIN_GROUP_MEMBERS} DISTINCT members are required;
 * - a node belongs to AT MOST one group: the new ids are first removed from any
 *   existing group, and any group that drops below the minimum is dissolved.
 *
 * Returns the unchanged `groups` and `id: null` when the request is rejected.
 */
export function addGroup(
  groups: GroupMeta[],
  nodeIds: string[],
  name: string,
): AddGroupResult {
  const trimmed = name.trim();
  const members = Array.from(new Set(nodeIds));
  if (trimmed.length === 0 || members.length < MIN_GROUP_MEMBERS) {
    return { groups, id: null };
  }
  // Steal these ids from any other group, dissolving ones left too small.
  const claimed = new Set(members);
  const rebased = pruneMembership(groups, (id) => !claimed.has(id));
  const id = newGroupId();
  return { groups: [...rebased, { id, name: trimmed, nodeIds: members }], id };
}

/**
 * Rename a group in place (immutably). A blank/whitespace-only name is rejected,
 * returning the unchanged array so the store can no-op without a history entry.
 */
export function renameGroupIn(
  groups: GroupMeta[],
  id: string,
  name: string,
): GroupMeta[] {
  const trimmed = name.trim();
  if (trimmed.length === 0) return groups;
  return groups.map((g) => (g.id === id ? { ...g, name: trimmed } : g));
}

/** Remove a group entirely (ungroup). Members are untouched in `nodes[]`. */
export function removeGroupIn(groups: GroupMeta[], id: string): GroupMeta[] {
  return groups.filter((g) => g.id !== id);
}

/**
 * Prune deleted node ids out of every group and dissolve groups that fall below
 * the minimum size. Used when nodes are removed so dangling membership and
 * orphaned single-member groups can't accumulate.
 */
export function pruneGroupsForNodes(
  groups: GroupMeta[],
  removedIds: Iterable<string>,
): GroupMeta[] {
  const removed = new Set(removedIds);
  if (removed.size === 0) return groups;
  return pruneMembership(groups, (id) => !removed.has(id));
}

/**
 * Drop any member id failing `keep`, then discard groups left below the minimum
 * member count. Returns a NEW array; never mutates inputs.
 */
function pruneMembership(
  groups: GroupMeta[],
  keep: (id: string) => boolean,
): GroupMeta[] {
  return groups
    .map((g) => ({ ...g, nodeIds: g.nodeIds.filter(keep) }))
    .filter((g) => g.nodeIds.length >= MIN_GROUP_MEMBERS);
}
