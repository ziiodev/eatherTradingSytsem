/**
 * Dual-target codegen client (MQL5 + Python) for Aether's `/api/eas` surface.
 *
 * Uses the shared `@/lib/api` helpers (cookie-JWT + CSRF on POST). The editor
 * generates from the IN-MEMORY graph, so it hits the PREVIEW (no-persist)
 * variants: `POST /api/eas/codegen/{mql5,python}` with body `{ graph, ea_name }`.
 *
 * The backend response is `{ target, ea_name, source }`; this module normalizes
 * it to a `{ code }` shape the editor's CodePane already consumes.
 *
 * The `graph` payload is the editor's `SerializedGraph` and MUST round-trip
 * verbatim — React-Flow `node.type:"custom"` + domain `data.type` are never
 * normalized here.
 */
import { apiPost, ApiError } from "@/lib/api";
import type { SerializedGraph } from "../_types/graph";

/** Raw backend codegen response. */
interface CodegenResponse {
  target: string;
  ea_name: string;
  source: string;
}

/** Normalized result the editor consumes (one pane per language). */
export interface CodegenResult {
  language: "mql5" | "python";
  code: string;
}

/** POST /api/eas/codegen/mql5 — generate MQL5 from a serialized graph (preview). */
export async function generateMql5(
  graph: SerializedGraph,
  eaName?: string,
): Promise<CodegenResult> {
  const res = await apiPost<CodegenResponse>("/api/eas/codegen/mql5", {
    graph,
    ea_name: eaName ?? "GeneratedEA",
  });
  return { language: "mql5", code: res.source };
}

/** POST /api/eas/codegen/python — generate pure-stdlib Python (preview). */
export async function generatePython(
  graph: SerializedGraph,
  eaName?: string,
): Promise<CodegenResult> {
  const res = await apiPost<CodegenResponse>("/api/eas/codegen/python", {
    graph,
    ea_name: eaName ?? "GeneratedEA",
  });
  return { language: "python", code: res.source };
}

/** Map an API error to a friendly Spanish message for the codegen UI surface. */
export function codegenErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 401:
        return "Tu sesión ha expirado. Vuelve a iniciar sesión para generar código.";
      case 422:
        return "El grafo de esta estrategia es inválido o está incompleto. Añade un nodo Start e inténtalo de nuevo.";
      default:
        return "La generación de código falló. Inténtalo de nuevo.";
    }
  }
  return "No se pudo contactar con el servidor. Inténtalo de nuevo.";
}
