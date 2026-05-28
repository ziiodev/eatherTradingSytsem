"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  createProject,
  type ProjectCreateInput,
} from "@/lib/projects";
import { ProjectForm } from "@/components/projects/ProjectForm";

export default function NewProjectPage(): React.JSX.Element {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(values: ProjectCreateInput): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createProject(values);
      toast.success("Proyecto creado");
      router.push(`/proyectos/${created.id}`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setError("Ya existe un proyecto con ese nombre.");
        } else if (err.status === 400 || err.status === 422) {
          setError(
            "Validación fallida en el servidor. Revisa los campos del formulario.",
          );
        } else if (err.status === 401) {
          setError("Sesión expirada. Vuelve a iniciar sesión.");
        } else {
          setError(`Error inesperado (${err.status})`);
        }
      } else {
        setError("Error de red. Intenta de nuevo.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          Nuevo proyecto
        </h1>
        <p className="text-sm text-[rgb(var(--foreground-muted))]">
          Define el proyecto. Cuando lo crees podrás activarlo desde su
          detalle.
        </p>
      </header>

      <ProjectForm
        mode="create"
        submitting={submitting}
        error={error}
        onSubmit={handleSubmit}
        onCancel={() => router.push("/proyectos")}
      />
    </section>
  );
}
