/**
 * Leaf module for palette constants shared between the sidebar (drag source)
 * and the canvas (drop target). Kept dependency-free so both `NodeSidebar` and
 * `FlowCanvas` can import it without a cycle.
 */

/** Custom MIME used to carry the node type from sidebar drag to canvas drop. */
export const NODE_DND_MIME = "application/x-tcn-node";
