// Re-export the generated OpenAPI schema as the canonical typed surface.
export type { paths, components, operations } from "./api";
// Convenience aliases for the common types future code will reach for.
export type { components as Schemas } from "./api";
