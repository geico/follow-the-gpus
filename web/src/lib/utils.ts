// shadcn/ui cn() helper (source copied by the shadcn CLI, ADR 0026).
// Copyright (c) 2023 shadcn. MIT License; see web/public/THIRD_PARTY_NOTICES.txt.

import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
