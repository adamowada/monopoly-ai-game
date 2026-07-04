import { Slot } from "@radix-ui/react-slot";
import type { ComponentPropsWithoutRef } from "react";

import { cn } from "../../lib/ui";

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  asChild?: boolean;
};

export function Button({ asChild = false, className, type = "button", ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md bg-teal-700 px-3 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-not-allowed disabled:bg-neutral-300 disabled:text-neutral-600",
        className,
      )}
      type={asChild ? undefined : type}
      {...props}
    />
  );
}
