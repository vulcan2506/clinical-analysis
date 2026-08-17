"""
topic_summarizer.py
────────────────────
Generates chunk-grounded, source-scoped topic summaries to replace the
current registry `summarized_description` — which today is an LLM summary
of short per-chunk descriptions (never raw chunk text), built by blending
every source doc's descriptions into one prompt (see registry.py's
build_registry). That blending is why the retrieval router structurally
couldn't surface some content (e.g. "coordination of benefits") — the
summary a query gets compared against was a compression of a compression,
diluted across unrelated source docs.

For every topic, and for each source-doc bracket it spans, this produces a
structured BehavioralProfile extracted from the ACTUAL chunk text — reusing
delta_analyzer.py's two-pass holistic + gap-fill extraction (same prompts,
same schema), just run per single source instead of only on cross-version
A/B pairs. Where delta_jobs_cache.json already has a cached profile for a
topic/side (from the last delta_analyzer.py run), that's reused directly —
no LLM calls — since it's the exact same extraction we'd otherwise redo.

Outputs:
  - data/output/topic_summaries/<source_doc>/<topic_slug>.md  (human-readable)
  - topic_registry.csv: `summarized_description` replaced with real,
    chunk-grounded content (concatenated across source brackets — feeds
    the ChromaDB "intelligence" overview collection, which wants breadth
    across all sources, not a single-source split)
  - topic_registry.csv: new `grounded_summary` column, bracketed per
    source doc like `description` already is (feeds retrieval_layer's
    per-source topic vector — precision, not blended)
  - enterprise_nested_topics.json patched in place with both fields.
    Patched, not regenerated — nested.py's clustering (parent/sub
    grouping) is left untouched so an eval comparison isolates this
    change instead of being confounded by a taxonomy reshuffle.
"""

import json
import logging
import os as _os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

import config
import llm_client
from delta_analyzer import (
    BehavioralProfile,
    _build_gap_fill_prompts,
    _build_holistic_prompts,
    _extract_version,
    _get_chunk_text,
    _get_qna_for_id,
    _merge_profiles,
    _parse_additions,
    _parse_profile,
    _render_profile,
    _validate_significance_level,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

NESTED_PATH   = config.NESTED_OUTPUT_PATH
REGISTRY_PATH = config.REGISTRY_PATH
# Stage 2 (patient) runs point this at fused_chunk_text.json — patient text
# with matched guideline text spliced in — instead of the raw ingest cache.
# Stage 1's own runs never set this var, so CHUNKS_PATH is unchanged there.
CHUNKS_PATH   = Path(_os.environ["STAGE2_FUSED_CHUNKS_PATH"]) if _os.environ.get("STAGE2_FUSED_CHUNKS_PATH") else config.CHUNKS_CACHE
GROUPED_PATH  = config.OUTPUT_DIR / "filtered_chunks.json"
CACHE_PATH    = config.OUTPUT_DIR / "delta_jobs_cache.json"
SUMMARY_DIR   = config.OUTPUT_DIR / "topic_summaries"

# Old `summarized_description` averaged ~250 chars corpus-wide (~420 for the
# topics we upgrade). A raw profile dump ran ~1900 chars — ~4.5x longer,
# which let enriched topics out-rank genuinely more relevant untouched ones
# on cosine similarity purely by having more content to match against
# (verified: Q5/Q19 regressed exactly this way). Capping keeps enriched
# topics length-comparable to what they're competing against, instead of
# structurally advantaged.
EMBED_CHAR_BUDGET = 800    # per source-bracket text (feeds grounded_summary / topic vector)
BLEND_CHAR_BUDGET = 1000   # final cross-bracket blend (feeds summarized_description / intelligence)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80] or "untitled"


def _strip_leading_label(text: str, label: str) -> str:
    """Drop leading repetitions of a topic's own label from its summary text.

    A topic's blended summary typically starts by restating the topic label,
    e.g. 'Foo. Foo: subtitle. ...' or 'Foo. Foo. content...'. Since the
    section heading already names the topic, those leading 'Label.' sentences
    are redundant — strip them, leaving 'Foo: subtitle. ...' or 'content...'.
    """
    if not text or not label:
        return text
    pat = re.compile(rf"^\s*({re.escape(label)}\.\s*)+", re.IGNORECASE)
    stripped = pat.sub("", text).strip()
    return stripped or text


def _split_brackets(raw) -> List[str]:
    """'[a] | [b]' -> ['a', 'b']; also accepts an already-split list."""
    items = raw if isinstance(raw, list) else (str(raw).split("] | [") if raw else [])
    return [str(item).strip().lstrip("[").rstrip("]") for item in items]


def _bracket_compound_ids(bracket: str) -> List[str]:
    return [p.strip() for p in bracket.split(",") if p.strip()]


def _bracket_text_and_ids(chunk_dict: Dict[str, str], bracket: str) -> Tuple[str, List[str]]:
    ids = _bracket_compound_ids(bracket)
    texts = [t for cid in ids if (t := _get_chunk_text(chunk_dict, cid))]
    return "\n\n".join(texts), ids


def _qna_for_bracket(qna_dict: Dict, ids: List[str]) -> list:
    seen_q, out = set(), []
    for cid in ids:
        for pair in _get_qna_for_id(qna_dict, cid):
            q = pair.get("q", "")
            if q and q not in seen_q:
                seen_q.add(q)
                out.append(pair)
    return out


def _find_bracket(all_brackets: List[Dict], label: str, doc: str) -> Optional[Dict]:
    """Find a bracket entry by (label, doc) pair."""
    for b in all_brackets:
        if b["label"] == label and b["doc"] == doc:
            return b
    return None


def _profile_to_embed_text(label: str, p: BehavioralProfile, char_budget: int = EMBED_CHAR_BUDGET) -> str:
    """
    Greedily fills up to char_budget in priority order (feature name, then
    behaviors, then new/requirements/deprecated items) so the most
    diagnostic content survives the cap rather than being truncated
    mid-sentence at an arbitrary cutoff.
    """
    candidates = [label, p.feature_name, *p.key_behaviors, *p.new_items, *p.requirements, *p.deprecated_items]
    parts: List[str] = []
    total = 0
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        add_len = len(c) + (2 if parts else 0)   # ". " separator once joined
        if parts and total + add_len > char_budget:
            break
        parts.append(c)
        total += add_len
    return ". ".join(parts)


def _truncate_at_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Prefer a sentence boundary (". " or ".  ") within the limit so the
    # blend never ends mid-sentence; fall back to the last space (the old
    # word-boundary cutoff) only if no sentence boundary exists.
    cut = text.rfind(". ", 0, limit)
    if cut <= 0:
        cut = text.rfind("  ", 0, limit)
    if cut <= 0:
        cut = text.rfind(" ", 0, limit)
    return text[:cut + 1 if cut > 0 else limit].rstrip()


def _load_cache() -> Dict[str, Dict]:
    if not CACHE_PATH.exists():
        return {}
    with open(CACHE_PATH, encoding="utf-8") as f:
        jobs = json.load(f)
    return {job["topic"]: job for job in jobs}


def _load_chunk_dict() -> Dict[str, str]:
    with open(CHUNKS_PATH, encoding="utf-8") as f:
        return {str(c["chunk_id"]): c["text"] for c in json.load(f)}


def _load_qna_dict() -> Dict[str, list]:
    qna_dict: Dict[str, list] = {}
    if GROUPED_PATH.exists():
        with open(GROUPED_PATH, encoding="utf-8") as f:
            for c in json.load(f):
                cid = str(c.get("chunk_id", ""))
                qna = c.get("qna", [])
                if cid and isinstance(qna, list) and qna:
                    qna_dict[cid] = qna
                    for part in cid.split("_"):
                        qna_dict.setdefault(part, qna)
    return qna_dict


def _get_domain_profile(taxonomy_data: dict) -> Tuple[str, str]:
    analyst_role, doc_purpose = "a healthcare IT analyst", "release note"
    try:
        import context_profiler
    except ImportError:
        return analyst_role, doc_purpose
    for parent in taxonomy_data["taxonomy"]:
        for sub in parent["sub_categories"]:
            for topic in sub["topics"]:
                docs = topic.get("source_docs") or []
                if docs:
                    profile = context_profiler.get_profile(docs[0])
                    if profile:
                        analyst_role = profile.get("analyst_role", analyst_role)
                        doc_purpose  = profile.get("document_purpose", doc_purpose)
                    return analyst_role, doc_purpose
    return analyst_role, doc_purpose


# ── Main ─────────────────────────────────────────────────────────────────────

def run_topic_summarization(cached_only: bool = False):
    """
    cached_only=True skips fresh extraction entirely — only brackets with a
    matching delta_jobs_cache.json entry get updated (zero LLM calls). Used
    to validate the mechanism/measure eval impact before paying for the much
    larger single-source-topic extraction pass.
    """
    log.info("Loading chunk text, QnA hints, delta cache, and taxonomy...")
    chunk_dict     = _load_chunk_dict()
    qna_dict       = _load_qna_dict()
    cache_by_topic = _load_cache()
    log.info(f"Delta cache: {len(cache_by_topic)} cached cross-version topic profiles available for reuse.")

    with open(NESTED_PATH, encoding="utf-8") as f:
        taxonomy_data = json.load(f)

    analyst_role, doc_purpose = _get_domain_profile(taxonomy_data)

    # Per-doc profile lookup, not the single corpus-wide analyst_role/
    # doc_purpose above — _get_domain_profile only ever returns the FIRST
    # topic's first source doc's profile, which silently mislabels every
    # other document in a mixed-domain corpus. More importantly: this
    # dict is threaded into each job as job["_profile"] below, which is
    # what _build_holistic_prompts/_build_gap_fill_prompts (imported from
    # delta_analyzer.py) actually key their DYNAMIC schema adaptation off
    # of via _get_holistic_prompt_template(job.get("_profile")) — without
    # it, those calls silently fall back to the fully-hardcoded
    # WebLogic/workbasket-flavored template regardless of domain, even
    # though the function signature suggests it's already domain-aware.
    import context_profiler
    profile_by_doc: Dict[str, Optional[dict]] = {}

    def _doc_profile(doc: str) -> Optional[dict]:
        if doc not in profile_by_doc:
            profile_by_doc[doc] = context_profiler.get_profile(doc)
        return profile_by_doc[doc]

    # ── Pass 1: walk every topic/source-doc bracket, reuse cache where possible ──
    fresh_jobs: List[Dict]  = []
    all_brackets: List[Dict] = []

    for parent in taxonomy_data["taxonomy"]:
        for sub in parent["sub_categories"]:
            for topic in sub["topics"]:
                label = topic.get("master_label", "")
                source_docs   = topic.get("source_docs") or []
                chunk_brackets = _split_brackets(topic.get("chunk_ids"))
                if not label or not source_docs or len(source_docs) != len(chunk_brackets):
                    continue

                cached = cache_by_topic.get(label)

                for doc, bracket in zip(source_docs, chunk_brackets):
                    text, ids = _bracket_text_and_ids(chunk_dict, bracket)
                    if not text:
                        continue

                    entry = {"label": label, "doc": doc, "profile": None}

                    if cached:
                        doc_version = _extract_version(doc)
                        for side in ("A", "B"):
                            if cached.get(f"v{side}") == doc_version:
                                pdata = cached.get(f"profile_{side}")
                                if pdata:
                                    entry["profile"] = BehavioralProfile(**pdata)
                                break

                    if entry["profile"] is None:
                        if cached_only:
                            continue   # drop — leave this bracket's topic on its old summary
                        doc_profile = _doc_profile(doc)
                        fresh_jobs.append({
                            "topic": label,
                            "text_A": text,
                            "qna_A": _qna_for_bracket(qna_dict, ids),
                            "_analyst_role": (doc_profile or {}).get("analyst_role", analyst_role),
                            "_doc_purpose":  (doc_profile or {}).get("document_purpose", doc_purpose),
                            "_profile":      doc_profile,
                            "_entry": entry,   # backref filled in after extraction
                        })

                    all_brackets.append(entry)

    n_cached = sum(1 for b in all_brackets if b["profile"] is not None)
    log.info(f"{len(all_brackets)} (topic, source) brackets total — "
             f"{n_cached} reused from delta cache, {len(fresh_jobs)} need fresh extraction.")

    # ── Pass 2: fresh holistic + gap-fill extraction (delta_analyzer's own passes) ──
    if fresh_jobs:
        log.info("Sub-pass 1a: holistic extraction...")
        raw_holistic = llm_client.generate_batch(
            _build_holistic_prompts(fresh_jobs, "A"), max_tokens=700,
            desc="Topic summary — holistic", stop=["```\n"], enable_thinking=False,
        )
        for job, raw in zip(fresh_jobs, raw_holistic):
            job["rough_profile_A"] = _parse_profile(raw)

        log.info("Sub-pass 1b: gap fill...")
        raw_gaps = llm_client.generate_batch(
            _build_gap_fill_prompts(fresh_jobs, "A"), max_tokens=500,
            desc="Topic summary — gap fill", stop=["```\n"], enable_thinking=False,
        )
        for job, raw in zip(fresh_jobs, raw_gaps):
            job["_entry"]["profile"] = _merge_profiles(job["rough_profile_A"], _parse_additions(raw))

        # ── Sub-pass 1c: keyword validation of significance_level ──
        for job in fresh_jobs:
            p = job["_entry"]["profile"]
            if p:
                job["_entry"]["profile"] = _validate_significance_level(p, job.get("text_A", ""))

    # ── Pass 3: render markdown, build per-topic embed text ──
    log.info("Rendering per-source markdown summaries...")
    by_topic_text: Dict[str, List[Tuple[str, str]]] = {}
    for entry in all_brackets:
        label, doc, p = entry["label"], entry["doc"], entry["profile"]
        field_labels = (_doc_profile(doc) or {}).get("field_labels")
        md = [f"# {label}", f"*Source: {doc}*\n", *_render_profile(p, field_labels)]
        out_dir = SUMMARY_DIR / _slugify(doc)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{_slugify(label)}.md").write_text("\n".join(md), encoding="utf-8")
        by_topic_text.setdefault(label, []).append((doc, _profile_to_embed_text(label, p)))

    def _blend(entries: List[Tuple[str, str]]) -> str:
        return _truncate_at_boundary(" ".join(t for _, t in entries), BLEND_CHAR_BUDGET)

    # ── Pass 4: patch topic_registry.csv ──
    log.info("Updating topic_registry.csv...")
    df = pd.read_csv(REGISTRY_PATH)
    grounded_col, blended_col, sig_col = [], [], []
    for _, row in df.iterrows():
        entries = by_topic_text.get(row["master_label"], [])
        if not entries:
            grounded_col.append("")
            blended_col.append(row.get("summarized_description", ""))
            sig_col.append(row.get("significance_level", ""))
            continue
        grounded_col.append(" | ".join(f"[{t}]" for _, t in entries))
        blended_col.append(_blend(entries))
        # Propagate significance_level from the first bracket's profile
        sl = ""
        for doc_name, _txt in entries:
            bracket = _find_bracket(all_brackets, row["master_label"], doc_name)
            if bracket and bracket.get("profile") and bracket["profile"].significance_level:
                sl = bracket["profile"].significance_level
                break
        sig_col.append(sl)
    df["grounded_summary"]       = grounded_col
    df["summarized_description"] = blended_col
    df["significance_level"]     = sig_col
    df.to_csv(REGISTRY_PATH, index=False)
    log.info(f"Registry updated → {REGISTRY_PATH}")

    # ── Pass 5: patch nested taxonomy JSON in place (no re-clustering) ──
    n_patched = 0
    for parent in taxonomy_data["taxonomy"]:
        for sub in parent["sub_categories"]:
            for topic in sub["topics"]:
                entries = by_topic_text.get(topic.get("master_label", ""), [])
                if not entries:
                    continue
                topic["grounded_summary"]       = " | ".join(f"[{t}]" for _, t in entries)
                topic["summarized_description"] = _blend(entries)
                # Propagate significance_level from the first bracket's profile
                for doc_name, _txt in entries:
                    bracket = _find_bracket(all_brackets, topic.get("master_label", ""), doc_name)
                    if bracket and bracket.get("profile"):
                        sl = bracket["profile"].significance_level
                        if sl:
                            topic["significance_level"] = sl
                            break
                n_patched += 1

    with open(NESTED_PATH, "w", encoding="utf-8") as f:
        json.dump(taxonomy_data, f, indent=4)
    log.info(f"Patched {n_patched} topics in {NESTED_PATH}")
    log.info(f"Markdown summaries written → {SUMMARY_DIR}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cached-only", action="store_true",
                         help="Only update topics with a matching delta_jobs_cache.json entry (no LLM calls).")
    args = parser.parse_args()
    run_topic_summarization(cached_only=args.cached_only)
