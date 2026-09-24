// src/lib/utils.ts — Hilfsfunktion für shadcn/ui.
//
// `cn()` kombiniert CSS-Klassen intelligent: Konflikte (z. B. zwei verschiedene
// padding-Klassen) werden von tailwind-merge aufgelöst, clsx kümmert sich um
// bedingte Klassen (cn("base", condition && "extra")).

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
