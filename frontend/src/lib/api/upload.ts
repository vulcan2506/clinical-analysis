import { API_BASE, ApiError } from "@/lib/api/client";

// corpusId omitted/"default" writes into today's single shared PDF dir
// (unchanged). A Stage 2 customer corpus id routes into that customer's own
// Stage 2/data/<id>/pdfs/ — the corpus must already exist (createPatientCorpus).
export async function uploadPdf(
  file: File,
  corpusId: string = "default"
): Promise<{ filename: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file);

  const url = `${API_BASE}/api/upload?corpus_id=${encodeURIComponent(corpusId)}`;
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `Upload failed (${res.status})`);
  }
  return res.json();
}
