"""
cross_corpus.py
────────────────
Live, request-time lookup of guideline-corpus content matching an arbitrary
query — no offline nearest-neighbor match table, no persisted fused
artifact. Replaces Stage 2/guideline_fusion.py's bespoke SentenceTransformer
+ cosine-argmax mechanism with the same router+reranker machinery this
process already uses for chat retrieval, so "which guideline content
matches this patient topic" is answered the same way, at the same
confidence, everywhere it's asked.

Two entry points:
  - lookup_guideline_chunks() — raw chunks, for chat's two-hop retrieval.
  - lookup_guideline_topics()  — chunks resolved back to their owning
    guideline TOPIC and that topic's already-computed summary, for the
    summarizer-merge/delta/evolution use cases (Stage 2, reached via
    cross_corpus_cli.py — see that file's docstring for why this module
    cannot be imported directly from Stage 2's process).

Nothing here writes to either corpus. Caching (if any) is the caller's
responsibility — see redis_cache.py's xcorp_* helpers.
"""

import hashlib
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

import config
import router as hnsw_router
import reranker
from build_index import parse_chunk_ids

log = logging.getLogger(__name__)

# ── Guideline output-dir resolution ─────────────────────────────────────────
# corpus_registry.py has no "output_dir" concept (only chroma_dir/index_dir —
# it's a retrieval-serving registry, not a pipeline-output registry), and
# there is exactly one guideline KB in this deployment, aliased onto
# "default"'s own storage (see corpus_registry.py's module docstring). So
# this module hardcodes the "guidelines" case against config.STAGE1_OUTPUT/
# config.REGISTRY_CSV rather than inventing a general corpus_id -> output_dir
# resolver for a single-target lookup. Extend if a second guideline KB is
# ever needed.
_GUIDELINE_REGISTRY_CSV = config.REGISTRY_CSV


# ── Reverse map + registry-row + KB-version singletons (per resolved path) ──

_reverse_map_lock = threading.Lock()
_reverse_maps: Dict[Path, Dict[str, str]] = {}
_registry_rows: Dict[Path, Dict[str, dict]] = {}
_kb_versions: Dict[Path, str] = {}


def _build_topic_reverse_map(registry_csv_path: Path) -> "tuple[Dict[str, str], Dict[str, dict]]":
    """
    Parses topic_registry.csv's chunk_ids column (e.g. "[1,32_33]" or
    "[9_10] | [15_16]" for multi-source-doc topics) into a flat
    {chunk_id: master_label} reverse map, reusing build_index.parse_chunk_ids
    — the SAME parser that already builds chunk_lookup.pkl/hnsw_meta.pkl, so
    the reverse map's keys are guaranteed to match whatever chunk_id strings
    router.route() actually returns (e.g. "0_1", "5_6_8", plain "2"/"3"),
    rather than risking a second, subtly-different bracket parser.

    Also returns a {master_label: row_dict} map so lookup_guideline_topics()
    can pull grounded_summary/summarized_description/source_docs without a
    second CSV read.
    """
    import pandas as pd

    df = pd.read_csv(registry_csv_path)
    reverse: Dict[str, str] = {}
    rows: Dict[str, dict] = {}
    for _, row in df.iterrows():
        label = row.get("master_label", "")
        if not label or not isinstance(label, str):
            continue
        rows[label] = row.to_dict()

        raw = row.get("chunk_ids", "")
        if not raw or not isinstance(raw, str):
            continue
        brackets = [b.strip() for b in raw.split("|") if b.strip()]
        for cid in parse_chunk_ids(brackets):
            reverse[cid] = label
    return reverse, rows


def _get_topic_reverse_map(registry_csv_path: Path = _GUIDELINE_REGISTRY_CSV) -> Dict[str, str]:
    if registry_csv_path not in _reverse_maps:
        with _reverse_map_lock:
            if registry_csv_path not in _reverse_maps:
                reverse, rows = _build_topic_reverse_map(registry_csv_path)
                _reverse_maps[registry_csv_path] = reverse
                _registry_rows[registry_csv_path] = rows
                log.info(
                    f"Guideline reverse map built — {len(reverse)} chunk_ids -> "
                    f"{len(rows)} topics ({registry_csv_path})"
                )
    return _reverse_maps[registry_csv_path]


def _get_registry_rows(registry_csv_path: Path = _GUIDELINE_REGISTRY_CSV) -> Dict[str, dict]:
    _get_topic_reverse_map(registry_csv_path)  # ensures both singletons are built together
    return _registry_rows[registry_csv_path]


def guideline_kb_version(registry_csv_path: Path = _GUIDELINE_REGISTRY_CSV) -> str:
    """
    sha256 of topic_registry.csv's own bytes — a content hash, not
    mtime/size, so a touch-without-change doesn't spuriously invalidate
    every cache entry keyed on this. Cached in-process; call reset_cache()
    after reprocessing the guideline KB to force recompute.
    """
    if registry_csv_path not in _kb_versions:
        with _reverse_map_lock:
            if registry_csv_path not in _kb_versions:
                digest = hashlib.sha256(registry_csv_path.read_bytes()).hexdigest()[:16]
                _kb_versions[registry_csv_path] = digest
    return _kb_versions[registry_csv_path]


def reset_cache(registry_csv_path: Optional[Path] = None) -> None:
    """
    Drops the reverse-map/registry-row/version singletons for
    registry_csv_path (or all of them) — call alongside the existing
    router.reset_router()/chroma_store.reset_store() calls whenever the
    guideline KB is reprocessed, so stale matches/versions aren't served.
    """
    with _reverse_map_lock:
        if registry_csv_path is None:
            _reverse_maps.clear()
            _registry_rows.clear()
            _kb_versions.clear()
        else:
            _reverse_maps.pop(registry_csv_path, None)
            _registry_rows.pop(registry_csv_path, None)
            _kb_versions.pop(registry_csv_path, None)


# ── Public lookups ───────────────────────────────────────────────────────────

# Cross-encoder (NeuML/biomedbert-base-reranker — see config.RERANKER_MODEL)
# rerank_score is a sigmoid probability in (0, 1], NOT the unbounded score the
# earlier ms-marco-MiniLM-L-6-v2 cross-encoder produced. The previous
# MIN_MATCH_SCORE=1.0 was calibrated against that old model's scale (genuine
# matches 1.47-3.77) and silently filtered out EVERY result once the reranker
# switched — the threshold must be on the current model's scale.
#
# Calibrated 2026-08-15 against real queries against the guideline KB with the
# biomedbert reranker: genuine matches (kidney function / lipid panel queries)
# cluster at 0.998-1.0; administrative/boilerplate topics (laboratory contact
# info, health department info, institutional header) topped out at 0.978.
# 0.99 sits cleanly in that gap. Recalibrate as more query/score data
# accumulates — this is the single knob controlling the guideline-lookup floor.
MIN_MATCH_SCORE = 0.99


def lookup_guideline_chunks(
    query: str,
    k: Optional[int] = None,
    corpus_id: str = "guidelines",
    min_score: Optional[float] = MIN_MATCH_SCORE,
) -> List[Dict]:
    """
    Route + rerank against corpus_id, live, every call — no offline match
    table. Returns raw chunk dicts (chunk_id, text, section_header,
    source_doc, rerank_score, score) — chat's two-hop retrieval consumes
    these directly, no topic resolution needed.

    min_score defaults to MIN_MATCH_SCORE — chunks scoring below it are
    dropped before k truncation, so a caller asking for k results never
    gets padded out with noise just to hit the count. Pass min_score=None
    to disable the floor entirely (e.g. for score-distribution diagnostics).
    """
    route_result = hnsw_router.route(query, corpus_id=corpus_id)
    chunks = route_result.get("chunks", [])
    if not chunks:
        return []
    ranked = reranker.rerank(query, chunks)
    if min_score is not None:
        ranked = [c for c in ranked if c.get("rerank_score", float("-inf")) >= min_score]
    return ranked[:k] if k else ranked


def lookup_guideline_topics(
    query: str,
    k: int = 3,
    corpus_id: str = "guidelines",
    min_score: Optional[float] = MIN_MATCH_SCORE,
) -> List[Dict]:
    """
    Same live route+rerank as lookup_guideline_chunks(), but resolves each
    hit's chunk_id back to its owning guideline TOPIC via the reverse map,
    dedups to one entry per topic (keeping the highest-scoring chunk), and
    returns up to k distinct topics with their already-computed summaries —
    the shape the summarizer-merge/delta/evolution use cases need.

    Orphan chunk_ids (retrieved but absent from the reverse map — e.g. a
    chunk grouping edge case) are skipped, not an error; a topic simply
    won't surface from that particular chunk hit. min_score is applied once,
    upstream, by lookup_guideline_chunks() — not re-filtered here.
    """
    overfetch_k = max(k * 4, 12)
    chunks = lookup_guideline_chunks(query, k=overfetch_k, corpus_id=corpus_id, min_score=min_score)
    if not chunks:
        return []

    reverse_map = _get_topic_reverse_map()
    registry_rows = _get_registry_rows()

    best_by_topic: Dict[str, Dict] = {}
    for c in chunks:
        cid = str(c.get("chunk_id", ""))
        label = reverse_map.get(cid)
        if not label:
            continue  # orphan chunk_id — no owning topic, skip
        score = c.get("rerank_score", 0.0)
        existing = best_by_topic.get(label)
        if existing is None or score > existing["score"]:
            row = registry_rows.get(label, {})
            best_by_topic[label] = {
                "master_label": label,
                "score": score,
                "chunk_id": cid,
                "chunk_text": c.get("text", ""),
                "grounded_summary": row.get("grounded_summary", ""),
                "summarized_description": row.get("summarized_description", ""),
                "source_docs": row.get("source_docs", ""),
            }

    ranked_topics = sorted(best_by_topic.values(), key=lambda t: t["score"], reverse=True)
    return ranked_topics[:k]
