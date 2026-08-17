import { apiFetch } from "@/lib/api/client";
import type { CorporaStatus } from "@/lib/types";

// Wraps GET /api/corpora — status of the permanent guidelines KB and every
// per-customer Stage 2 patient corpus (retrieval_layer/api_server.py).
export function getCorpora(): Promise<CorporaStatus> {
  return apiFetch("/api/corpora");
}

// Wraps POST /api/corpora/patients — creates a new customer KB working
// directory (Stage 2/data/<id>/) and returns its generated id.
export function createPatientCorpus(label: string): Promise<{ id: string; label: string }> {
  return apiFetch("/api/corpora/patients", {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}
