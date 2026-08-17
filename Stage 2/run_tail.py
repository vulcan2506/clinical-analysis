"""
Stage 2/run_tail.py
────────────────────
Near-duplicate of Stage 1/run_tail.py's step sequence — not a call-through,
because some steps are semantically Stage-1-only. Every reused step function
is imported directly from Stage 1's run_tail module, not copied:

  1. step_filter()                  — reused verbatim
  2. step_parent_relationship()     — reused verbatim (patient's own
                                       intra-taxonomy relationships)
     step_cross_corpus_relationship — OMITTED. Explicitly rejected as a
                                       fusion mechanism (too heavy, only
                                       produces a linking file, never merges
                                       data) — left untouched and uncalled.
  3. build_fused_chunks()           — NEW: enriches each patient chunk with
                                       matched guideline context from Stage 1's
                                       guideline KB via cross_corpus_cli.py.
                                       Sets STAGE2_FUSED_CHUNKS_PATH so
                                       topic_summarizer.py reads fused text.
4. step_topic_summarizer()        — now reads fused chunks (patient text +
                                        guideline context) instead of patient-only
   4b. step_guideline_grounding()    — NEW: patient–guideline semantic enrichment
                                        engine (guideline_grounding.py). Classifies
                                        each patient topic against the guideline
                                        hierarchy (hybrid local-embedding +
                                        cross_corpus_cli retrieval), validates
                                        applicability against the whole patient
                                        universe, and writes §22 grounding records
                                        to <patient output>/guideline_grounded_summaries/
                                        plus an additive guideline_grounded_summary
                                        patch on enterprise_nested_topics.json.
                                        hierarchy_summarizer is untouched.
   5. step_hierarchy_summarizer()    — reused verbatim.
     step_evolution_analysis()      — OMITTED (Stage 1's own real
                                       version-evolution use case doesn't
                                       apply to a single patient report).
     step_convert_delta()           — OMITTED (fixed 2026-07-17 — was
                                       silently crashing every patient
                                       reprocess: it unconditionally reads
                                       delta_jobs_cache.json, which used to
                                       be written by the now-deleted
                                       step_delta_analyzer_fusion(). Nothing
                                       in Stage 2's pipeline produces real
                                       version-delta content anymore — a
                                       single patient report has no
                                       "versions" to compare, and fusion
                                       delta moved to request-time
                                       (fusion_worker.py, never touches this
                                       file). step_chroma()'s ingest_delta()
                                       already handles a missing
                                       delta_reports/ dir gracefully (warns,
                                       skips) — the patient corpus's Chroma
                                       "delta" collection is simply empty,
                                       which is correct.
  5b. hierarchy_topic_merge.py       — appends each topic's full topic-summary content (step 5's
                                       output) directly under its one-line bullet inside the
                                       hierarchy summaries (step 6's output), so the shallow
                                       overview gains the dense per-topic detail. Pure file
                                       merge — no LLM calls.
  6. step_build_index()             — reused verbatim (subprocess call
                                       inherits this process's env vars).
  7. step_chroma()                  — reused verbatim, same reasoning.
     step_eval() / step_cache_preset() — OMITTED. No patient-specific golden
                                       eval set exists; eval.py --compare is
                                       tied to Stage 1's own domain.

Guideline-fusion awareness was moved to request-time (cross_corpus.py,
fusion_worker.py, api_server.py's enhanced-summary/guideline-conformance
endpoints) but the pipeline summaries are now also enriched with guideline
context via build_fused_chunks — this gives the summarizer access to
guideline text during extraction, producing summaries that reference both
the patient's values AND the relevant clinical guidelines.

Usage (after Stage 2/main.py has completed for the same STAGE2_PATIENT_ID):
    cd "Stage 2"
    STAGE2_PATIENT_ID=patient_123 python run_tail.py
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage2_config as s2cfg  # noqa: E402

s2cfg.setup_patient_env(os.environ.get("STAGE2_PATIENT_ID", "default"))
s2cfg.put_stage1_first_on_path()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("stage2_run_tail")

import config  # noqa: E402  (Stage 1's config.py, resolved via sys.path priority)
import run_tail as stage1_run_tail  # noqa: E402  (resolves to Stage 1/run_tail.py)
from build_fused_chunks import build_fused_chunks as step_build_fused_chunks  # noqa: E402
from guideline_grounding import run_guideline_grounding as step_guideline_grounding  # noqa: E402

step_filter = stage1_run_tail.step_filter
step_parent_relationship = stage1_run_tail.step_parent_relationship
step_topic_summarizer = stage1_run_tail.step_topic_summarizer
step_hierarchy_summarizer = stage1_run_tail.step_hierarchy_summarizer
step_hierarchy_topic_merge = stage1_run_tail.step_hierarchy_topic_merge
step_build_index = stage1_run_tail.step_build_index
step_chroma = stage1_run_tail.step_chroma


def main():
    nested = config.OUTPUT_DIR / "enterprise_nested_topics.json"
    filtered = config.OUTPUT_DIR / "filtered_chunks.json"
    registry = config.OUTPUT_DIR / "topic_registry.csv"
    for p in [nested, filtered, registry]:
        if not p.exists():
            log.error(f"Prerequisite missing: {p.name} — run Stage 2/main.py first")
            sys.exit(1)

    patient_id = os.environ.get("STAGE2_PATIENT_ID", "default")

    step_filter()
    step_parent_relationship()
    step_build_fused_chunks(patient_id)  # enriches chunks with guideline context
    step_topic_summarizer()
    step_guideline_grounding(patient_id)  # patient–guideline semantic enrichment (§22 records)
    step_hierarchy_summarizer()
    step_hierarchy_topic_merge()
    step_build_index()
    step_chroma()

    log.info("\n✅ Stage 2 pipeline tail complete.")


if __name__ == "__main__":
    main()
