"use client";

import { useState, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import {
  ChevronRight,
  RefreshCw,
  AlertCircle,
  Sparkles,
  ClipboardCheck,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion";
import { getKnowledgeFile } from "@/lib/api/knowledge";
import { getEnhancedSummary, getGuidelineConformance } from "@/lib/api/topics";
import type { TaxonomyRoot, TaxonomyTopic, FusionChangeType, SignificanceLevel } from "@/lib/types";
import { cn } from "@/lib/utils";

// context_profiler.py's fixed 4-value fusion_change_types vocabulary —
// color-coded for quick visual scanning, not a generic string badge.
const CHANGE_TYPE_STYLE: Record<string, string> = {
  "Concordant with Guideline": "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  "Deviates — Clinically Significant": "bg-destructive/10 text-destructive",
  "Deviates — Borderline": "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  "Guideline Silent on This Case": "bg-muted text-muted-foreground",
};

function ChangeTypeBadge({ changeType }: { changeType: string | null }) {
  if (!changeType) return null;
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent font-normal", CHANGE_TYPE_STYLE[changeType] ?? "bg-muted text-muted-foreground")}
    >
      {changeType}
    </Badge>
  );
}

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

function SignificanceBadge({ level }: { level: SignificanceLevel | undefined }) {
  if (!level) return null;
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent font-normal text-[10px] px-1.5 py-0", SIGNIFICANCE_STYLE[level] ?? "bg-muted text-muted-foreground")}
    >
      {SIGNIFICANCE_LABEL[level] ?? level}
    </Badge>
  );
}

// Ordered from most urgent to least — drives the dropdown stacking order.
const SIGNIFICANCE_ORDER: SignificanceLevel[] = [
  "critical",
  "high",
  "moderate",
  "low",
  "informational",
];

// Both new endpoints are explicit, on-demand, potentially-slow actions (not
// auto-fetched on mount) — useMutation + the idle/pending/error/success
// pattern already established by VisualizeDialog.tsx's generateMutation.

function EnhancedSummaryPanel({ corpusId, topicLabel }: { corpusId: string; topicLabel: string }) {
  const mutation = useMutation({
    mutationFn: () => getEnhancedSummary(corpusId, topicLabel),
  });

  if (!mutation.data && !mutation.isPending && !mutation.isError) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 w-fit gap-1.5 text-xs"
        onClick={() => mutation.mutate()}
      >
        <Sparkles className="size-3.5" /> Enhanced Summary
      </Button>
    );
  }

  const matches = mutation.data?.matched_guideline_topics ?? [];

  return (
    <div className="rounded-md border bg-muted/20 p-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-medium">
          <Sparkles className="size-3.5" /> Enhanced Summary
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 gap-1 text-[11px]"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          <RefreshCw className={cn("size-3", mutation.isPending && "animate-spin")} /> Refresh
        </Button>
      </div>
      {mutation.isPending && (
        <div className="flex items-center gap-2 py-3 text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Matching with guideline knowledge…
        </div>
      )}
      {mutation.isError && (
        <p className="text-destructive">
          {mutation.error instanceof Error ? mutation.error.message : "Failed to load grounding"}
        </p>
      )}
      {mutation.data && (
        <div className="space-y-3">
          <div>
            <p className="mb-1 font-medium text-muted-foreground">Patient finding (unchanged)</p>
            <p className="leading-relaxed text-foreground">{mutation.data.patient_summary}</p>
          </div>
          {matches.length === 0 && (
            <p className="text-muted-foreground">
              No guideline topic matched this finding (status: {mutation.data.grounding_status}).
            </p>
          )}
          {matches.map((m) => (
            <div key={m.label} className="space-y-1 rounded-md border bg-background p-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{m.label}</span>
                {m.match_type && (
                  <Badge variant="outline" className="font-normal text-[10px]">
                    {m.match_type}
                  </Badge>
                )}
                {m.score != null && (
                  <span className="text-muted-foreground">score {m.score.toFixed(3)}</span>
                )}
              </div>
              {m.match_reason && (
                <p className="text-muted-foreground">{m.match_reason}</p>
              )}
              {m.source_docs.length > 0 && (
                <p className="text-[10px] text-muted-foreground">
                  Source: {m.source_docs.join(", ")}
                </p>
              )}
              {m.summary && (
                <div className="prose prose-sm max-w-none dark:prose-invert [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                  <ReactMarkdown>{m.summary}</ReactMarkdown>
                </div>
              )}
            </div>
          ))}
          {mutation.data.from_cache && (
            <p className="text-[10px] text-muted-foreground">Served from cache</p>
          )}
        </div>
      )}
    </div>
  );
}

function GuidelineConformancePanel({ corpusId, topicLabel }: { corpusId: string; topicLabel: string }) {
  const mutation = useMutation({
    mutationFn: () => getGuidelineConformance(corpusId, topicLabel),
  });

  if (!mutation.data && !mutation.isPending && !mutation.isError) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 w-fit gap-1.5 text-xs"
        onClick={() => mutation.mutate()}
      >
        <ClipboardCheck className="size-3.5" /> Guideline Conformance
      </Button>
    );
  }

  const evolutionByGuideline = mutation.data
    ? new Map(mutation.data.evolution.map((e) => [e.guideline_label, e]))
    : null;

  return (
    <div className="rounded-md border bg-muted/20 p-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-medium">
          <ClipboardCheck className="size-3.5" /> Guideline Conformance
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 gap-1 text-[11px]"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          <RefreshCw className={cn("size-3", mutation.isPending && "animate-spin")} /> Refresh
        </Button>
      </div>
      {mutation.isPending && (
        <div className="flex items-center gap-2 py-3 text-muted-foreground">
          <Loader2 className="size-3.5 shrink-0 animate-spin" />
          Running delta + evolution analysis against matched guidelines — can take up to a
          couple of minutes on a cold cache…
        </div>
      )}
      {mutation.isError && (
        <p className="text-destructive">
          {mutation.error instanceof Error ? mutation.error.message : "Failed to run conformance analysis"}
        </p>
      )}
      {mutation.data && mutation.data.matches === 0 && (
        <p className="text-muted-foreground">No matching guideline content found for this topic.</p>
      )}
      {mutation.data && mutation.data.matches > 0 && evolutionByGuideline && (
        <div className="space-y-3">
          {mutation.data.delta.map((d, i) => {
            const evo = evolutionByGuideline.get(d.guideline_label);
            return (
              <div key={`${d.guideline_label}-${i}`} className="space-y-1.5 border-l-2 border-border pl-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <ChangeTypeBadge changeType={d.change_type} />
                  {d.confidence && (
                    <span className="text-[10px] text-muted-foreground">confidence: {d.confidence}</span>
                  )}
                </div>
                {d.analysis && <p className="text-foreground">{d.analysis}</p>}
                {d.key_differences && d.key_differences.length > 0 && (
                  <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                    {d.key_differences.map((kd, j) => (
                      <li key={j}>{kd}</li>
                    ))}
                  </ul>
                )}
                {evo && (
                  <div className="mt-1.5 rounded bg-background p-2 space-y-1.5">
                    {evo.clinical_finding && (
                      <p className="text-foreground">
                        <span className="font-medium text-muted-foreground">Patient Finding: </span>
                        {evo.clinical_finding}
                      </p>
                    )}
                    {evo.guideline_context && (
                      <p className="text-foreground">
                        <span className="font-medium text-muted-foreground">Guideline Standard: </span>
                        {evo.guideline_context}
                      </p>
                    )}
                    {evo.clinical_significance && (
                      <Badge variant="outline" className="border-transparent font-normal text-[10px] bg-amber-500/10 text-amber-600 dark:text-amber-400">
                        {evo.clinical_significance}
                      </Badge>
                    )}
                    <p className="font-medium text-foreground">{evo.narrative}</p>
                    {evo.value_added.length > 0 && (
                      <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                        {evo.value_added.map((v, j) => (
                          <li key={j}>{v}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {mutation.data.from_cache && (
            <p className="text-[10px] text-muted-foreground">Served from cache</p>
          )}
        </div>
      )}
    </div>
  );
}

function TopicRow({ corpusId, topic }: { corpusId: string; topic: TaxonomyTopic }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border p-2.5">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-1.5 text-left">
        <ChevronRight className={cn("mt-0.5 size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium">{topic.master_label}</p>
            <SignificanceBadge level={topic.significance_level} />
          </div>
          <p className="line-clamp-2 text-xs text-muted-foreground">
            {topic.summarized_description || topic.description}
          </p>
        </div>
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-2 pl-5">
          <EnhancedSummaryPanel corpusId={corpusId} topicLabel={topic.master_label} />
          <GuidelineConformancePanel corpusId={corpusId} topicLabel={topic.master_label} />
        </div>
      )}
    </div>
  );
}

interface PatientTopicPanelProps {
  corpusId: string;
}

export function PatientTopicPanel({ corpusId }: PatientTopicPanelProps) {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["knowledge-file", corpusId, "enterprise_nested_topics.json"],
    queryFn: () => getKnowledgeFile("enterprise_nested_topics.json", corpusId),
  });

  const taxonomy = data?.type === "json" ? (data.content as TaxonomyRoot) : null;

  // Group topics by significance level, preserving parent/sub-category structure
  const topicsByLevel = useMemo(() => {
    const grouped: Record<SignificanceLevel, TaxonomyRoot["taxonomy"]> = {
      critical: [], high: [], moderate: [], low: [], informational: [],
    };
    if (!taxonomy) return grouped;

    for (const parent of taxonomy.taxonomy) {
      for (const sub of parent.sub_categories) {
        const byLevel: Record<SignificanceLevel, TaxonomyTopic[]> = {
          critical: [], high: [], moderate: [], low: [], informational: [],
        };
        for (const topic of sub.topics) {
          const level = topic.significance_level ?? "informational";
          byLevel[level].push(topic);
        }
        for (const level of SIGNIFICANCE_ORDER) {
          if (byLevel[level].length === 0) continue;
          // Find or create the parent entry in this level's bucket
          let parentEntry = grouped[level].find((p) => p.parent_category_name === parent.parent_category_name);
          if (!parentEntry) {
            parentEntry = { parent_category_name: parent.parent_category_name, parent_category_description: parent.parent_category_description, sub_categories: [] };
            grouped[level].push(parentEntry);
          }
          parentEntry.sub_categories.push({ sub_category_name: sub.sub_category_name, sub_category_description: sub.sub_category_description, topics: byLevel[level] });
        }
      }
    }
    return grouped;
  }, [taxonomy]);

  const levelCounts = useMemo(() => {
    const counts: Partial<Record<SignificanceLevel, number>> = {};
    for (const level of SIGNIFICANCE_ORDER) {
      let n = 0;
      for (const parent of topicsByLevel[level]) {
        for (const sub of parent.sub_categories) n += sub.topics.length;
      }
      if (n > 0) counts[level] = n;
    }
    return counts;
  }, [topicsByLevel]);

  const activeLevels = SIGNIFICANCE_ORDER.filter((l) => (levelCounts[l] ?? 0) > 0);

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <h3 className="text-sm font-semibold">Patient Topics</h3>
        <Button variant="ghost" size="icon" className="size-6" onClick={() => refetch()} aria-label="Refresh">
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} />
        </Button>
      </div>

      <div className="min-w-0 flex-1 overflow-y-auto p-2">
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
              Couldn&apos;t load topics — run Process first.
            </div>
            <Button size="sm" variant="outline" className="h-7 gap-1" onClick={() => refetch()}>
              <RefreshCw className="size-3" /> Retry
            </Button>
          </div>
        )}
        {taxonomy?.taxonomy.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">No topics yet — run Process above.</p>
        )}

        {activeLevels.length > 0 && (
          <Accordion multiple>
            {activeLevels.map((level) => (
              <AccordionItem key={level} value={level}>
                <AccordionTrigger className="py-2 text-xs">
                  <span className="flex items-center gap-2">
                    <span
                      className={cn(
                        "inline-block size-2 rounded-full",
                        level === "critical" && "bg-red-500",
                        level === "high" && "bg-orange-500",
                        level === "moderate" && "bg-amber-500",
                        level === "low" && "bg-blue-500",
                        level === "informational" && "bg-muted-foreground",
                      )}
                    />
                    {SIGNIFICANCE_LABEL[level]}
                    <Badge variant="outline" className="border-transparent font-normal text-[10px] px-1.5 py-0 text-muted-foreground">
                      {levelCounts[level]}
                    </Badge>
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  {topicsByLevel[level].map((parent) => (
                    <div key={parent.parent_category_name} className="mb-2">
                      <p className="mb-1 px-1 text-[11px] font-semibold text-muted-foreground">
                        {parent.parent_category_name}
                      </p>
                      {parent.sub_categories.map((sub) =>
                        sub.topics.map((topic) => (
                          <div key={topic.master_label} className="mb-1.5">
                            <TopicRow corpusId={corpusId} topic={topic} />
                          </div>
                        ))
                      )}
                    </div>
                  ))}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </div>
    </div>
  );
}
