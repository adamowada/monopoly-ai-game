import { Slot } from "@radix-ui/react-slot";
import type { ComponentPropsWithoutRef } from "react";

import { cn } from "../../lib/ui";

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  asChild?: boolean;
  variant?: "primary" | "secondary" | "danger" | "warning" | "dark" | "ai" | "ghost";
};

const buttonVariants: Record<NonNullable<ButtonProps["variant"]>, string> = {
  primary:
    "bg-teal-700 text-white shadow-sm hover:bg-teal-800 focus-visible:outline-teal-700 disabled:bg-neutral-300 disabled:text-neutral-600",
  secondary:
    "bg-white text-neutral-800 ring-1 ring-inset ring-neutral-300 hover:bg-neutral-100 focus-visible:outline-teal-700 disabled:bg-neutral-100 disabled:text-neutral-500 disabled:ring-neutral-200",
  danger:
    "bg-rose-700 text-white shadow-sm hover:bg-rose-800 focus-visible:outline-rose-700 disabled:bg-neutral-300 disabled:text-neutral-600",
  warning:
    "bg-amber-700 text-white shadow-sm hover:bg-amber-800 focus-visible:outline-amber-700 disabled:bg-neutral-300 disabled:text-neutral-600",
  dark:
    "bg-neutral-800 text-white shadow-sm hover:bg-neutral-900 focus-visible:outline-neutral-800 disabled:bg-neutral-300 disabled:text-neutral-600",
  ai:
    "bg-purple-700 text-white shadow-sm hover:bg-purple-800 focus-visible:outline-purple-700 disabled:bg-neutral-300 disabled:text-neutral-600",
  ghost:
    "bg-transparent text-neutral-700 hover:bg-neutral-100 focus-visible:outline-teal-700 disabled:text-neutral-500",
};

export function Button({ asChild = false, className, type = "button", variant = "primary", ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed",
        buttonVariants[variant],
        className,
      )}
      data-button-variant={variant}
      type={asChild ? undefined : type}
      {...props}
    />
  );
}
