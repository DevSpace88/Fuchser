// src/lib/utils.ts — helper function for shadcn/ui.
//
// `cn()` combines CSS classes intelligently: conflicts (e.g. two different
// padding classes) are resolved by tailwind-merge; clsx takes care of
// conditional classes (cn("base", condition && "extra")).

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
