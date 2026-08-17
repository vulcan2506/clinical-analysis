import { apiFetch } from "@/lib/api/client";
import type { EnhancedSummaryResponse, GuidelineConformanceResponse } from "@/lib/types";

// Must match retrieval_layer/api_server.py's _slugify() exactly — the
// backend resolves topic_slug by slugifying every topic_registry.csv row's
// master_label with that same algorithm and comparing, so any drift here
// means a topic silently 404s.
export function slugifyTopic(label: string): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "customer";
}

export function getEnhancedSummary(
  corpusId: string,
  topicLabel: string
): Promise<EnhancedSummaryResponse> {
  return apiFetch(
    `/api/corpora/patients/${encodeURIComponent(corpusId)}/topics/${encodeURIComponent(
      slugifyTopic(topicLabel)
    )}/enhanced-summary`
  );
}

export function getGuidelineConformance(
  corpusId: string,
  topicLabel: string
): Promise<GuidelineConformanceResponse> {
  return apiFetch(
    `/api/corpora/patients/${encodeURIComponent(corpusId)}/topics/${encodeURIComponent(
      slugifyTopic(topicLabel)
    )}/guideline-conformance`
  );
}
