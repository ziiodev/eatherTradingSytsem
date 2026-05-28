"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Headless tabs with the shadcn-compatible part API. We re-implement
 * (rather than pulling Radix) because the dashboard's tabs needs are
 * modest: a single horizontal bar, no keyboard menus, no portals.
 *
 * Parts:
 *   <Tabs value={...} onValueChange={...}>
 *     <TabsList>
 *       <TabsTrigger value="general">General</TabsTrigger>
 *       ...
 *     </TabsList>
 *     <TabsContent value="general">…</TabsContent>
 *     ...
 *   </Tabs>
 */

interface TabsContextValue {
  value: string;
  onValueChange: (value: string) => void;
}

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabs(): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error("Tabs sub-components must render inside <Tabs>.");
  }
  return ctx;
}

export interface TabsProps {
  value: string;
  onValueChange: (value: string) => void;
  className?: string;
  children?: React.ReactNode;
}

function Tabs({
  value,
  onValueChange,
  className,
  children,
}: TabsProps): React.JSX.Element {
  const ctx = React.useMemo(
    () => ({ value, onValueChange }),
    [value, onValueChange],
  );
  return (
    <TabsContext.Provider value={ctx}>
      <div className={cn("flex flex-col gap-4", className)}>{children}</div>
    </TabsContext.Provider>
  );
}

const TabsList = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    role="tablist"
    className={cn(
      "inline-flex items-center gap-1 rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--background-elevated))] p-1",
      className,
    )}
    {...props}
  />
));
TabsList.displayName = "TabsList";

export interface TabsTriggerProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

const TabsTrigger = React.forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ value, className, children, ...props }, ref) => {
    const { value: active, onValueChange } = useTabs();
    const isActive = active === value;
    return (
      <button
        ref={ref}
        role="tab"
        type="button"
        aria-selected={isActive}
        data-state={isActive ? "active" : "inactive"}
        className={cn(
          "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1 text-sm font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent))]",
          isActive
            ? "bg-[rgb(var(--background))] text-[rgb(var(--foreground))] shadow-sm"
            : "text-[rgb(var(--foreground-muted))] hover:text-[rgb(var(--foreground))]",
          className,
        )}
        onClick={(e) => {
          props.onClick?.(e);
          onValueChange(value);
        }}
        {...props}
      >
        {children}
      </button>
    );
  },
);
TabsTrigger.displayName = "TabsTrigger";

export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string;
}

const TabsContent = React.forwardRef<HTMLDivElement, TabsContentProps>(
  ({ value, className, ...props }, ref) => {
    const { value: active } = useTabs();
    if (active !== value) return null;
    return (
      <div
        ref={ref}
        role="tabpanel"
        className={cn("flex flex-col gap-4", className)}
        {...props}
      />
    );
  },
);
TabsContent.displayName = "TabsContent";

export { Tabs, TabsList, TabsTrigger, TabsContent };
