import { apiFetch } from "@/lib/api/client";
import type { KnowledgeNode, KnowledgeFileContent } from "@/lib/types";

export async function getKnowledgeFiles(corpusId: string = "default"): Promise<KnowledgeNode[]> {
  const { tree } = await apiFetch<{ tree: KnowledgeNode[] }>(
    `/api/knowledge/files?corpus_id=${encodeURIComponent(corpusId)}`
  );
  return tree;
}

export function getKnowledgeFile(path: string, corpusId: string = "default"): Promise<KnowledgeFileContent> {
  return apiFetch(
    `/api/knowledge/file?path=${encodeURIComponent(path)}&corpus_id=${encodeURIComponent(corpusId)}`
  );
}
