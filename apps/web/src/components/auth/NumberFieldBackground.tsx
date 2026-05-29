"use client";

import { useEffect, useRef } from "react";

/**
 * NumberFieldBackground — fondo decorativo de la pantalla de login.
 *
 * Pinta una rejilla muy dispersa de dígitos (0-9) en un canvas. Cada
 * dígito tiene una opacidad baja por defecto y se vuelve más brillante
 * cuanto más cerca está del cursor. Cada celda re-rolea su dígito cada
 * pocos segundos para dar sensación de vida sin ser intrusivo.
 *
 * Reglas de diseño:
 *   - Discreto: opacidad base ≤ 0.07; opacidad cerca del cursor ≤ 0.45.
 *   - Usa el font-mono y el color foreground del tema (GitHub Dark).
 *   - 0 CPU cuando la pestaña no es visible (pausa el RAF loop).
 *   - Respeta ``prefers-reduced-motion``: degrada a una rejilla estática.
 *   - Auto-redimensiona en resize del viewport.
 *   - pointer-events: none — nunca intercepta clicks del formulario.
 *
 * Charter: ningún input, ningún datos sensibles. Pura decoración.
 */
export function NumberFieldBackground(): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const ctx = canvas.getContext("2d");
    if (ctx === null) return;
    // Re-bind to non-null locals so the inner closures don't widen the
    // type back to ``HTMLCanvasElement | null`` / ``CanvasRenderingContext2D | null``
    // (TS doesn't propagate the outer narrowing into nested function
    // declarations).
    const cv: HTMLCanvasElement = canvas;
    const cx: CanvasRenderingContext2D = ctx;

    // ──────────────────────────────────────────────────────────────
    // Estado interno (en clausuras para evitar re-renders de React).
    // ──────────────────────────────────────────────────────────────
    const STEP = 28; // distancia entre dígitos en px
    const INFLUENCE = 140; // radio del cursor en px
    const REROLL_INTERVAL_MS = 2400; // cada cuánto rota un dígito
    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    // mouse: viewport coords; offscreen por defecto para que el primer
    // frame no encienda nada antes de que el usuario mueva el ratón.
    let mouseX = -10_000;
    let mouseY = -10_000;
    // Una rejilla de dígitos (0-9 como char). Se llena en `layout()`.
    let cells: string[][] = [];
    // Timestamp por celda para escalonar el reroll.
    let nextReroll: number[][] = [];
    let lastFrame = 0;
    let rafId = 0;
    let running = true;

    // ──────────────────────────────────────────────────────────────
    function layout(): void {
      const rect = cv.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      cv.width = Math.floor(width * dpr);
      cv.height = Math.floor(height * dpr);
      cx.setTransform(dpr, 0, 0, dpr, 0, 0);
      // Rebuild grid
      const cols = Math.ceil(width / STEP) + 1;
      const rows = Math.ceil(height / STEP) + 1;
      cells = Array.from({ length: rows }, () =>
        Array.from({ length: cols }, () =>
          String(Math.floor(Math.random() * 10)),
        ),
      );
      const now = performance.now();
      nextReroll = Array.from({ length: rows }, () =>
        Array.from(
          { length: cols },
          () => now + Math.random() * REROLL_INTERVAL_MS,
        ),
      );
    }

    function draw(now: number): void {
      if (!running) return;
      // Limitar a ~30 FPS — más que suficiente para esto y ahorra batería.
      const dt = now - lastFrame;
      if (dt < 33) {
        rafId = window.requestAnimationFrame(draw);
        return;
      }
      lastFrame = now;
      cx.clearRect(0, 0, width, height);
      cx.font =
        '12px ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", monospace';
      cx.textBaseline = "middle";
      cx.textAlign = "center";
      const r2 = INFLUENCE * INFLUENCE;
      const rows = cells.length;
      for (let i = 0; i < rows; i += 1) {
        const row = cells[i];
        const rollRow = nextReroll[i];
        if (row === undefined || rollRow === undefined) continue;
        const y = i * STEP;
        for (let j = 0; j < row.length; j += 1) {
          const x = j * STEP;
          // Re-roll perezoso por celda.
          if (now >= (rollRow[j] ?? 0)) {
            row[j] = String(Math.floor(Math.random() * 10));
            rollRow[j] =
              now + REROLL_INTERVAL_MS * (0.6 + Math.random() * 0.8);
          }
          const dx = x - mouseX;
          const dy = y - mouseY;
          const d2 = dx * dx + dy * dy;
          // Opacidad base muy baja; crece hasta 0.45 cuando el cursor está
          // sobre la celda. Por debajo del influence radius una curva
          // suave (1 - d/R)^2 para que el "halo" sea más físico que lineal.
          let alpha = 0.05;
          if (d2 < r2) {
            const t = 1 - Math.sqrt(d2) / INFLUENCE;
            alpha = 0.05 + t * t * 0.4;
          }
          // GitHub Dark foreground RGB (mismo que el tema CSS var).
          cx.fillStyle = `rgba(230, 237, 243, ${alpha.toFixed(3)})`;
          cx.fillText(row[j] ?? "0", x, y);
        }
      }
      if (!reduceMotion) {
        rafId = window.requestAnimationFrame(draw);
      }
    }

    // ──────────────────────────────────────────────────────────────
    // Listeners
    // ──────────────────────────────────────────────────────────────
    function onMouseMove(e: MouseEvent): void {
      const rect = cv.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = e.clientY - rect.top;
    }
    function onMouseLeave(): void {
      mouseX = -10_000;
      mouseY = -10_000;
    }
    function onResize(): void {
      layout();
    }
    function onVisibilityChange(): void {
      if (document.hidden) {
        running = false;
        if (rafId !== 0) {
          window.cancelAnimationFrame(rafId);
          rafId = 0;
        }
      } else if (!running) {
        running = true;
        lastFrame = 0;
        rafId = window.requestAnimationFrame(draw);
      }
    }

    layout();
    rafId = window.requestAnimationFrame(draw);

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseleave", onMouseLeave);
    window.addEventListener("resize", onResize);
    document.addEventListener("visibilitychange", onVisibilityChange);

    return (): void => {
      running = false;
      if (rafId !== 0) window.cancelAnimationFrame(rafId);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseleave", onMouseLeave);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      // pointer-events-none asegura que el formulario sigue siendo clickable
      // a través del canvas. fixed + inset-0 cubre el viewport entero.
      className="pointer-events-none fixed inset-0 z-0 h-full w-full"
    />
  );
}

export default NumberFieldBackground;
