"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Folder, FileText, FileJson, RefreshCw, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion";
import { getKnowledgeFiles, getKnowledgeFile } from "@/lib/api/knowledge";
import type { KnowledgeNode, TaxonomyRoot, SignificanceLevel } from "@/lib/types";
import { FileViewerDialog } from "@/components/knowledge/FileViewerDialog";
import { cn } from "@/lib/utils";

// ── Significance-level styling (mirrors PatientTopicPanel) ──────────────────

const SIGNIFICANCE_STYLE: Record<SignificanceLevel, string> = {
  critical:      "bg-red-500/15 text-red-600 dark:text-red-400",
  high:          "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  moderate:      "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  low:           "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  informational: "bg-muted text-muted-foreground",
};

const SIGNIFICANCE_LABEL: Record<SignificanceLevel, string> = {
  critical:      "Critical",
  high:          "High",
  moderate:      "Moderate",
  low:           "Low",
  informational: "Informational",
};

const SIGNIFICANCE_ORDER: SignificanceLevel[] = [
  "critical", "high", "moderate", "low", "informational",
];

const SIGNIFICANCE_DOT: Record<SignificanceLevel, string> = {
  critical:      "bg-red-500",
  high:          "bg-orange-500",
  moderate:      "bg-amber-500",
  low:           "bg-blue-500",
  informational: "bg-muted-foreground",
};

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Strip .md extension and normalise to a file-slug for matching. */
function fileSlug(name: string): string {
  return name.replace(/\.md$/, "").toLowerCase().replace(/[-_\s]+/g, "_");
}

/** Build a mapping from file-slug → { significance_level, master_label, source_doc } */
function buildTopicMetaMap(taxonomy: TaxonomyRoot) {
  const map = new Map<string, { level: SignificanceLevel; label: string; source: string }>();
  for (const parent of taxonomy.taxonomy) {
    for (const sub of parent.sub_categories) {
      for (const topic of sub.topics) {
        const slug = fileSlug(topic.master_label);
        const source = topic.source_docs?.[0] ?? parent.parent_category_name;
        map.set(slug, {
          level: topic.significance_level ?? "informational",
          label: topic.master_label,
          source,
        });
      }
    }
  }
  return map;
}

/** Recursively count all files under a node. */
function countFiles(node: KnowledgeNode): number {
  if (node.type === "file") return 1;
  return node.children.reduce((sum, c) => sum + countFiles(c), 0);
}

/** Collect all files from a directory node (recursively). */
function collectFiles(node: KnowledgeNode): KnowledgeNode[] {
  if (node.type === "file") return [node];
  return node.children.flatMap(collectFiles);
}

// ── File row ────────────────────────────────────────────────────────────────

function FileRow({
  node,
  onOpenFile,
  meta,
}: {
  node: KnowledgeNode;
  onOpenFile: (path: string, displayName: string) => void;
  meta?: { level: SignificanceLevel; label: string; source: string };
}) {
  if (node.type !== "file") return null;
  const Icon = node.extension === ".json" ? FileJson : FileText;
  return (
    <button
      type="button"
      onClick={() => onOpenFile(node.path, node.display_name)}
      className="flex w-full min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
    >
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">
        {meta?.label ?? node.display_name}
      </span>
      {meta && (
        <Badge
          variant="outline"
          className={cn(
            "shrink-0 border-transparent font-normal text-[10px] px-1.5 py-0",
            SIGNIFICANCE_STYLE[meta.level],
          )}
        >
          {SIGNIFICANCE_LABEL[meta.level]}
        </Badge>
      )}
    </button>
  );
}

// ── Sub-tree (non-topic-summaries directories) ──────────────────────────────

function SubTreeNode({
  node,
  depth,
  onOpenFile,
  topicMetaMap,
}: {
  node: KnowledgeNode;
  depth: number;
  onOpenFile: (path: string, displayName: string) => void;
  topicMetaMap?: Map<string, { level: SignificanceLevel; label: string; source: string }>;
}) {
  const [open, setOpen] = useState(depth === 0);

  if (node.type === "file") {
    const slug = fileSlug(node.name);
    return <FileRow node={node} onOpenFile={onOpenFile} meta={topicMetaMap?.get(slug)} />;
  }

  // Special handling for topic_summaries directory — group by significance level
  if (node.name === "topic_summaries" && topicMetaMap && topicMetaMap.size > 0) {
    return <TopicSummariesGrouped node={node} onOpenFile={onOpenFile} topicMetaMap={topicMetaMap} />;
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-muted"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        <span className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")}>▸</span>
        <Folder className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate">{node.display_name}</span>
        <span className="ml-auto shrink-0 text-xs font-normal text-muted-foreground">
          {countFiles(node)}
        </span>
      </button>
      {open &&
        node.children.map((child) => (
          <SubTreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            onOpenFile={onOpenFile}
            topicMetaMap={topicMetaMap}
          />
        ))}
    </div>
  );
}

// ── Topic summaries grouped by significance level ───────────────────────────

function TopicSummariesGrouped({
  node,
  onOpenFile,
  topicMetaMap,
}: {
  node: KnowledgeNode;
  onOpenFile: (path: string, displayName: string) => void;
  topicMetaMap: Map<string, { level: SignificanceLevel; label: string; source: string }>;
}) {
  // Collect all .md files from all PDF subfolders
  const allFiles = useMemo(() => collectFiles(node).filter((f) => f.type === "file"), [node]);

  // Group by significance level
  const grouped = useMemo(() => {
    const buckets: Record<SignificanceLevel, KnowledgeNode[]> = {
      critical: [], high: [], moderate: [], low: [], informational: [],
    };
    for (const file of allFiles) {
      const slug = fileSlug(file.name);
      const meta = topicMetaMap.get(slug);
      const level = meta?.level ?? "informational";
      buckets[level].push(file);
    }
    return buckets;
  }, [allFiles, topicMetaMap]);

  const levelCounts = useMemo(() => {
    const counts: Partial<Record<SignificanceLevel, number>> = {};
    for (const level of SIGNIFICANCE_ORDER) {
      if (grouped[level].length > 0) counts[level] = grouped[level].length;
    }
    return counts;
  }, [grouped]);

  const activeLevels = SIGNIFICANCE_ORDER.filter((l) => (levelCounts[l] ?? 0) > 0);

  if (activeLevels.length === 0) {
    // No meta mapping available — fall back to flat list
    return (
      <div className="ml-5">
        {allFiles.map((f) => (
          <FileRow key={f.path} node={f} onOpenFile={onOpenFile} />
        ))}
      </div>
    );
  }

  return (
    <div className="ml-2">
      <Accordion multiple>
        {activeLevels.map((level) => (
          <AccordionItem key={level} value={level}>
            <AccordionTrigger className="py-1.5 text-xs">
              <span className="flex items-center gap-2">
                <span className={cn("inline-block size-2 rounded-full", SIGNIFICANCE_DOT[level])} />
                {SIGNIFICANCE_LABEL[level]}
                <Badge
                  variant="outline"
                  className="border-transparent font-normal text-[10px] px-1.5 py-0 text-muted-foreground"
                >
                  {grouped[level].length}
                </Badge>
              </span>
            </AccordionTrigger>
            <AccordionContent>
              {grouped[level].map((file) => {
                const slug = fileSlug(file.name);
                const meta = topicMetaMap.get(slug);
                return (
                  <FileRow
                    key={file.path}
                    node={file}
                    onOpenFile={onOpenFile}
                    meta={meta}
                  />
                );
              })}
            </AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

interface KnowledgeExplorerProps {
  corpusId?: string;
}

export function KnowledgeExplorer({ corpusId = "default" }: KnowledgeExplorerProps) {
  const [openFile, setOpenFile] = useState<{ path: string; displayName: string } | null>(null);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["knowledge-files", corpusId],
    queryFn: () => getKnowledgeFiles(corpusId),
  });

  // For patient corpora, fetch enterprise_nested_topics.json to get significance mappings
  const isPatientCorpus = corpusId !== "default" && corpusId !== "guidelines";
  const { data: topicData } = useQuery({
    queryKey: ["knowledge-file", corpusId, "enterprise_nested_topics.json"],
    queryFn: () => getKnowledgeFile("enterprise_nested_topics.json", corpusId),
    enabled: isPatientCorpus,
  });

  const topicMetaMap = useMemo(() => {
    if (!topicData || topicData.type !== "json") return undefined;
    try {
      return buildTopicMetaMap(topicData.content as TaxonomyRoot);
    } catch {
      return undefined;
    }
  }, [topicData]);

  const topLevelFolders = data?.filter((n) => n.type === "directory") ?? [];
  const topLevelFiles = data?.filter((n) => n.type === "file") ?? [];

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <h3 className="text-sm font-semibold">Knowledge Explorer</h3>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => refetch()}
          aria-label="Refresh"
        >
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} />
        </Button>
      </div>

      <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto p-1.5">
        {isLoading && (
          <div className="flex flex-col gap-2 p-2">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-3/4" />
          </div>
        )}
        {isError && (
          <div className="flex flex-col items-start gap-2 p-3 text-xs text-destructive">
            <div className="flex items-center gap-2">
              <AlertCircle className="size-3.5 shrink-0" />
              Couldn&apos;t reach the backend — is api_server.py running?
            </div>
            <Button size="sm" variant="outline" className="h-7 gap-1" onClick={() => refetch()}>
              <RefreshCw className="size-3" /> Retry
            </Button>
          </div>
        )}
        {data?.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">
            No knowledge artifacts yet — run the pipeline first (Document Processing above).
          </p>
        )}

        {topLevelFolders.length > 0 && (
          <Accordion multiple>
            {topLevelFolders.map((node) => {
              const fileCount = countFiles(node);
              return (
                <AccordionItem key={node.path} value={node.path}>
                  <AccordionTrigger className="py-2 text-xs">
                    <span className="flex items-center gap-2">
                      <Folder className="size-3.5 shrink-0 text-muted-foreground" />
                      {node.display_name}
                      <Badge variant="outline" className="border-transparent font-normal text-[10px] px-1.5 py-0 text-muted-foreground">
                        {fileCount}
                      </Badge>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    {node.children.map((child) => (
                      <SubTreeNode
                        key={child.path}
                        node={child}
                        depth={1}
                        onOpenFile={(path, displayName) => setOpenFile({ path, displayName })}
                        topicMetaMap={topicMetaMap}
                      />
                    ))}
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}

        {topLevelFiles.length > 0 && (
          <div className="mt-1">
            {topLevelFiles.map((node) => {
              if (node.type !== "file") return null;
              const slug = fileSlug(node.name);
              const meta = topicMetaMap?.get(slug);
              return (
                <FileRow
                  key={node.path}
                  node={node}
                  onOpenFile={(path, displayName) => setOpenFile({ path, displayName })}
                  meta={meta}
                />
              );
            })}
          </div>
        )}
      </div>

      <FileViewerDialog
        path={openFile?.path ?? null}
        displayName={openFile?.displayName ?? ""}
        onClose={() => setOpenFile(null)}
        corpusId={corpusId}
      />
    </div>
  );
}
