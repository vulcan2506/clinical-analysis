import { apiFetch } from "@/lib/api/client";
import type { SettingsStatus } from "@/lib/types";

export function getSettingsStatus(): Promise<SettingsStatus> {
  return apiFetch("/api/settings/status");
}
