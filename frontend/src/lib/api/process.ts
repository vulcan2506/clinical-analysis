import { apiFetch } from "@/lib/api/client";
import type { ProcessJobStatus } from "@/lib/types";

// Triggers a background subprocess chain for the given corpus_id:
// "default" -> Stage 1's main.py -> run_tail.py --skip-eval (today's single
// corpus). "guidelines" -> the same, scoped to Stage 1/default_clinical/,
// plus a third step building the guideline topic-embeddings index. Any other
// value -> Stage 2's main.py -> run_tail.py for that customer corpus id.
// Reprocesses the ENTIRE corpus each time (no incremental/single-file mode).
export function startProcessing(corpusId: string = "default"): Promise<{ job_id: string }> {
  return apiFetch("/api/process", {
    method: "POST",
    body: JSON.stringify({ corpus_id: corpusId }),
  });
}

export function getProcessStatus(jobId: string): Promise<ProcessJobStatus> {
  return apiFetch(`/api/process/${jobId}`);
}

// Clears the given corpus (source PDFs, generated output, vector store) so a
// fresh set of PDFs can be processed without merging with what was there
// before. "guidelines" is refused server-side — that KB is permanent.
export function resetCorpus(corpusId: string = "default"): Promise<{ status: string; message: string }> {
  return apiFetch("/api/reset", {
    method: "POST",
    body: JSON.stringify({ confirm: true, corpus_id: corpusId }),
  });
}
