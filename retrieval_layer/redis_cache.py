"""
redis_cache.py
──────────────
Response cache in front of retrieve() + answer generation, backed by the
local Redis instance (already running as a systemd service — no setup
needed). Two jobs:

  1. gate_and_retrieve(query, best_of=3) — a DYNAMIC routing gate, not a
     static per-category lookup table. Earlier version of this file used
     select_method(query), a fixed if/else keyed off keyword classifiers
     and a one-time offline composite score per category — which only ever
     "knows how to route" the shapes of query it was tuned against, and
     silently has nothing to fall back on once real traffic drifts outside
     the 13-53 queries it was calibrated on.

     This version instead mirrors retriever.py's own confidence ladder
     (config.CONFIDENCE_THRESHOLD + _top_rerank_score(), see
     _retrieve_specific()): run pipeline first (cheap — it's also the first
     leg of merged/merged_all anyway), check ITS OWN live rerank confidence,
     and only escalate to a richer/pooled method if that confidence is
     actually low for THIS query, right now. That generalizes to any future
     query for free, because the gate reacts to a runtime measurement of the
     query's own retrieval quality — not a keyword match against a fixed set
     of categories. select_method() is kept below only as a fallback labeler
     for reporting/inspection, not as the thing that decides routing anymore.

     "Cheap first pass" is now best-of-`best_of` (default 3) reformulations
     of the query, not a single retrieve() call — reuses retriever.
     retrieve_best_of_n(), which reformulates the query 3 ways and runs all
     3 through the pipeline in parallel, keeping whichever scores highest.
     The confidence check and any escalation below then operate on that
     winning reformulation's result, same as before. Pass best_of=0 (or 1)
     to fall back to a single plain retrieve() call, e.g. for a quick
     latency-sensitive path. detailed mode (retrieve_detailed) deliberately
     does NOT get best-of-N by default — it already pools 3 methods per
     call; stacking 3 reformulations on top of that would be 9 retrieval
     calls per query for a use case that already trades cost for coverage.

  2. answer_query(query, mode) — cache-checked entry point with two modes,
     both dynamic, neither a static lookup:

       mode="concise"  (default) — gate_and_retrieve(): stays on cheap
                         pipeline unless its own confidence is low, then
                         escalates. "Doesn't drift" — tight by default.
       mode="detailed"           — retrieve_detailed(): always the fullest
                         pool (merged_all), no confidence check — thorough
                         by default.

     Per the full 53-query x 6-method eval (deep_eval_full_6method.md),
     merged_all is actually the single best method on Correctness, Recall,
     AND Conciseness at that scale — it is not "detailed but bloated". Its
     one consistent weakness across both eval runs is Faithfulness (worst of
     all six methods) — pooling occasionally states irrelevant content as if
     related. So "detailed" mode trades a small faithfulness risk for
     thoroughness, not thoroughness for verbosity.

     Cache hit → return instantly from Redis (no LLM call at all); miss →
     retrieve per the selected mode + generate an answer, same generation
     call as deep_eval.py's generate_answer(), and return it (NOT
     auto-cached — caching is a deliberate, separate seeding step via
     build_cache_preset.py, so the demo's "cached vs uncached" split stays
     exactly as curated). Each mode is cached under its own key, so the same
     question can hold two different cached answers, one per mode.

Usage:
    import redis_cache
    result = redis_cache.answer_query("How does claim adjudication work?")               # concise
    result = redis_cache.answer_query("How does claim adjudication work?", mode="detailed")
    result["from_cache"]   # True/False
    result["answer"]       # str
    result["method"]       # which method actually served it (post-hoc, not pre-decided)
"""

import hashlib
import json
import logging
import time
from typing import Dict, Optional

import redis

import config
import retriever
import planner
import llm_client

log = logging.getLogger(__name__)

_client: Optional["redis.Redis"] = None


def _get_client() -> "redis.Redis":
    global _client
    if _client is None:
        if config.REDIS_URL:
            _client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        else:
            _client = redis.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT, db=config.REDIS_DB,
                decode_responses=True,
            )
    return _client


def _normalize(query: str) -> str:
    return " ".join(query.strip().lower().split())


def cache_key(query: str, mode: str = "concise", corpus_id: str = "default") -> str:
    """
    corpus_id is part of the key so an identical query string against two
    different corpora (e.g. "guidelines" vs. a Stage 2 patient corpus) can't
    collide and serve a cross-corpus-wrong cached answer — previously the
    key was hashed from mode+query only, with no corpus awareness at all.
    """
    digest = hashlib.sha256(f"{corpus_id}:{mode}:{_normalize(query)}".encode("utf-8")).hexdigest()[:24]
    return f"{config.REDIS_KEY_PREFIX}{digest}"


# ── Dynamic gate (confidence-based, not category-based) ────────────────────────

# corpus_id defaults to "default" here ONLY for the prefer_method path in
# answer_query() below, which stays uncorpus-aware (unchanged, pre-existing
# scope limit). gate_and_retrieve()'s own escalation call (below) always
# passes corpus_id explicitly — see its docstring for why that one was a
# real, confirmed bug (silent fallback to "default", not just an unused
# default) and had to be fixed rather than left as a documented gap.
METHOD_FNS = {
    "pipeline":    lambda q, corpus_id="default": retriever.retrieve(q, corpus_id=corpus_id),
    "naive":       lambda q, corpus_id="default": retriever.retrieve_naive(q, corpus_id=corpus_id),
    "traditional": lambda q, corpus_id="default": retriever.retrieve_overlap(q, corpus_id=corpus_id),
    "merged":      lambda q, corpus_id="default": retriever.retrieve_merged(q, corpus_id=corpus_id),
    "merged_all":  lambda q, corpus_id="default": retriever.retrieve_merged_all(q, corpus_id=corpus_id),
}


def select_method(query: str) -> str:
    """
    Offline/reporting-only label for "what category would this query hit,
    and which method did the deep-eval pilot favor for that category" —
    kept for build_cache_preset.py's manifest and for anyone wanting a
    quick classification without running retrieval. NOT used by
    gate_and_retrieve()/answer_query() to decide routing anymore — see the
    module docstring for why a static category lookup was replaced.
    """
    if retriever._is_cross_corpus_query(query):
        return "pipeline"
    if retriever._is_relationship_query(query):
        return "merged_all"
    if retriever._is_evolution_query(query):
        return "pipeline"
    intent = retriever.classify(query)
    if intent == "delta":
        return "merged"
    if intent == "intelligence":
        return "pipeline"
    return "traditional"


def gate_and_retrieve(
    query: str,
    best_of: int = 3,
    corpus_id: Optional[str] = None,
    active_patient_corpus_id: Optional[str] = None,
    original_query: Optional[str] = None,
) -> Dict:
    """
    Run pipeline first — cheap, and also the first leg of merged/merged_all
    anyway — and check its own live rerank confidence via the SAME mechanism
    retriever.py's confidence ladder already uses (config.CONFIDENCE_THRESHOLD,
    _top_rerank_score()). Confident -> use pipeline's answer directly (fast,
    cheap, and per the deep-eval pilot the most concise/faithful method when
    it's actually finding good matches). Not confident -> escalate.

    Which rung to escalate to (once escalation is already triggered by low
    confidence) still uses retriever's own relationship/delta classifiers —
    same as retriever.py's ladder does to pick its own escalation order —
    but that only decides WHICH pooled method to try, not WHETHER to escalate
    at all. The whether is 100% dynamic, so a query nobody anticipated still
    gets routed sensibly: high confidence -> stays cheap, low confidence ->
    gets more pooled context, regardless of its category.

    best_of (default 3): the "cheap pipeline" pass is itself a best-of-N
    reformulation (retriever.retrieve_best_of_n) rather than one retrieve()
    call — 3 parallel reformulations of the query, keeping whichever one's
    top rerank score is highest. The confidence check and escalation above
    then run against that winning result. Pass 0 or 1 to skip reformulation
    and use a single plain retrieve() call instead.

    corpus_id / active_patient_corpus_id: resolved once up front via
    retriever._classify_corpus_target() (or corpus_id used as-is if given)
    and applied to BOTH the pipeline pass and any escalation rung.

    Fixed 2026-07-17 — this used to be a real, confirmed bug, not just a
    documented gap: escalation (naive/traditional/merged/merged_all via
    METHOD_FNS) silently dropped to "default" regardless of the actual
    resolved_corpus_id, AND the returned dict's "corpus_id" field was
    overwritten with resolved_corpus_id afterward anyway — so a
    low-confidence query against a patient corpus (exactly the queries most
    likely to need escalation, since they're the ones the cheap pipeline
    pass couldn't answer confidently) would silently retrieve from the
    guidelines-only "default" corpus while the API response kept claiming
    the patient corpus was used. Verified directly: "Can you analyze
    patient report and suggest key findings?" against a real patient corpus
    scored well below CONFIDENCE_THRESHOLD on the cheap pass, triggered
    this exact path, and returned guideline-only citations with zero
    patient content.
    """
    resolved_corpus_id = corpus_id or retriever._classify_corpus_target(query, active_patient_corpus_id)

    if best_of and best_of > 1:
        pipeline_result = retriever.retrieve_best_of_n(query, n=best_of, corpus_id=resolved_corpus_id, active_patient_corpus_id=active_patient_corpus_id, original_query=original_query)
    else:
        pipeline_result = retriever.retrieve(query, corpus_id=resolved_corpus_id, active_patient_corpus_id=active_patient_corpus_id, original_query=original_query)
    top_score = retriever._top_rerank_score(pipeline_result)

    if top_score >= config.CONFIDENCE_THRESHOLD:
        pipeline_result = dict(pipeline_result)
        pipeline_result["_gated_method"] = "pipeline"
        pipeline_result["_gated_confidence"] = top_score
        return pipeline_result

    if retriever._is_relationship_query(query):
        method = "merged_all"
    elif retriever.classify(query) == "delta":
        method = "merged"
    else:
        method = "merged_all"   # generic escalation: pool everything available

    result = dict(METHOD_FNS[method](query, corpus_id=resolved_corpus_id))
    result["_gated_method"] = method
    result["_gated_confidence"] = top_score
    result["corpus_id"] = resolved_corpus_id
    return result


def retrieve_detailed(
    query: str,
    corpus_id: Optional[str] = None,
    active_patient_corpus_id: Optional[str] = None,
    original_query: Optional[str] = None,
) -> Dict:
    """
    "Detailed/explanatory" mode — always uses merged_all, no confidence
    check at all. Per the full 53-query x 6-method eval (deep_eval_full_6method.md),
    merged_all is the strongest single method on Correctness (0.998), Recall
    (0.976), Precision@5 (0.917), and even Conciseness (0.826, best of all six —
    it is NOT the "bloated" method at this scale). Its one real, consistent
    weakness across both the 13-query pilot and the full 53-query run is
    Faithfulness (0.895 here, the worst of the six) — pooling occasionally
    pulls in irrelevant content and states it as if related. Use this mode
    when the caller explicitly wants the most thorough answer available and
    accepts that faithfulness risk, as opposed to gate_and_retrieve()'s
    concise/"doesn't drift" mode, which only reaches for merged_all when
    pipeline's own confidence says it's actually needed.

    After pooling, always hands the result to planner.explore() — Adaptive
    Evidence Escalation: the planner assesses the pooled evidence on EVERY
    call (as of 2026-07-10, not gated by confidence), and only actually loops
    fetching more (up to config.PLANNER_MAX_HOPS hops, each hop's gap-queries
    re-routed fresh, not scoped to this query's own parent) if that
    assessment says evidence is incomplete or confidence is still low. A
    no-op assessment (evidence already sufficient) returns merged_all's
    result unchanged except for a harmless extra LLM round-trip. Entirely a
    no-op, including that round-trip, unless config.PLANNER_ENABLED is True
    — see planner.py's module docstring and config.py's PLANNER_ENABLED
    comment for the evaluation this decision was based on.

    Fixed 2026-07-17 — same bug class as gate_and_retrieve()'s escalation
    path (see its docstring): this call neither accepted nor threaded
    corpus_id, so retrieve_merged_all() always resolved "default" internally
    (no active_patient_corpus_id ever reached it), and planner.explore()'s
    own expansion hops were separately hardcoded to "default" regardless.
    Detailed Mode against a patient corpus was therefore guidelines-only
    end to end, not just on the seed retrieval.
    """
    resolved_corpus_id = corpus_id or retriever._classify_corpus_target(query, active_patient_corpus_id)
    result = dict(retriever.retrieve_merged_all(query, corpus_id=resolved_corpus_id, active_patient_corpus_id=active_patient_corpus_id, original_query=original_query))
    result = planner.explore(query, result, corpus_id=resolved_corpus_id)
    result["_gated_method"] = "merged_all+explore" if result.get("_explored") else "merged_all"
    result["corpus_id"] = resolved_corpus_id
    return result


# ── Answer generation (mirrors deep_eval.py's generate_answer) ────────────────

def _generate_answer(query: str, result: Dict, max_tokens: int = 900) -> str:
    """
    Scaffolds the answer around result["_filled_concepts"] when present —
    the sub-topics planner.explore() identified as missing AND actually
    found supporting evidence for (set only on escalation; see planner.py).
    Deliberately does NOT scaffold around every concept the planner named as
    missing — only ones with real retrieved evidence behind them. Naming an
    unfilled gap here would invite the model to write about a concept it has
    nothing grounding it in, which is exactly the hallucination/drift risk
    this whole feature exists to avoid, not produce.
    """
    context = result["context"]
    filled = result.get("_filled_concepts")
    if filled:
        outline = "\n".join(f"- {c}" for c in dict.fromkeys(filled))  # de-dup, keep order
        context += (
            "\n\nThe context above was expanded to also cover these specific "
            "sub-topics — organize your answer to address each one using ONLY "
            "the context provided (do not mention or speculate about any other "
            "sub-topic not covered above):\n" + outline
        )
    return llm_client.chat(
        f"{context}\n\nQuestion: {query}\n\n"
        "Note: If the question contains meta-references (e.g., \"previous question asked\", "
        "\"above\", \"this topic\"), interpret them as referring to the clinical subject matter "
        "evident from the provided context. Answer based solely on the information in the context.",
        system_prompt=result["system_prompt"],
        max_tokens=max_tokens,
    )


# ── Cache read/write ────────────────────────────────────────────────────────────

def get_cached(query: str, mode: str = "concise", corpus_id: str = "default") -> Optional[Dict]:
    raw = _get_client().get(cache_key(query, mode, corpus_id))
    return json.loads(raw) if raw else None


def set_cached(
    query: str, method: str, answer: str, latency_s: float,
    mode: str = "concise", corpus_id: str = "default",
) -> None:
    payload = {
        "query": query, "method": method, "answer": answer, "mode": mode,
        "corpus_id": corpus_id, "latency_s_at_seed": latency_s, "cached_at": time.time(),
    }
    key = cache_key(query, mode, corpus_id)
    if config.REDIS_TTL_SECONDS:
        _get_client().setex(key, config.REDIS_TTL_SECONDS, json.dumps(payload))
    else:
        _get_client().set(key, json.dumps(payload))


# ── Cross-corpus derived-output cache (merge/delta/evolution/chat_hop2) ────────
# A DIFFERENT cache than the answer cache above, with different semantics:
# auto-write-on-miss with a real TTL, not a curated no-expiry demo preset —
# this exists purely as a performance layer for expensive live cross_corpus
# lookups + LLM calls, keyed so it self-invalidates the moment the guideline
# KB actually changes. Reuses _get_client()'s exact connection setup; gets
# its own key namespace ("xcorp:") so it never collides with cache_key()'s
# answer-cache keys.

XCORP_TTL_SECONDS = 604_800  # 7 days — starting default, recalibrate once real usage patterns are known


def xcorp_cache_key(task_type: str, entity_id: str, guideline_kb_version: str, prompt_version: str = "v1") -> str:
    """
    task_type in {"merge", "delta", "evolution", "chat_hop2", "lookup"}.
    entity_id: patient-topic slug for merge/delta/evolution; a hash of
    (resolved_corpus_id, hop2_query) for chat_hop2. guideline_kb_version
    comes from cross_corpus.guideline_kb_version() — a content hash of
    topic_registry.csv, so this key changes automatically the moment the
    guideline KB is reprocessed, with no explicit invalidation step needed.
    """
    digest = hashlib.sha256(
        f"{task_type}:{entity_id}:{guideline_kb_version}:{prompt_version}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{config.REDIS_KEY_PREFIX}xcorp:{task_type}:{digest}"


def get_xcorp_cached(
    task_type: str, entity_id: str, guideline_kb_version: str, prompt_version: str = "v1"
) -> Optional[dict]:
    raw = _get_client().get(xcorp_cache_key(task_type, entity_id, guideline_kb_version, prompt_version))
    return json.loads(raw) if raw else None


def set_xcorp_cached(
    task_type: str, entity_id: str, guideline_kb_version: str, payload: dict, prompt_version: str = "v1"
) -> None:
    key = xcorp_cache_key(task_type, entity_id, guideline_kb_version, prompt_version)
    _get_client().setex(key, XCORP_TTL_SECONDS, json.dumps(payload))


def answer_query(
    query: str,
    mode: str = "concise",
    prefer_method: Optional[str] = None,
    best_of: int = 3,
    corpus_id: Optional[str] = None,
    active_patient_corpus_id: Optional[str] = None,
    original_query: Optional[str] = None,
) -> Dict:
    """
    Cache-checked entry point, now with two modes (each cached separately —
    the same question can hold two different cached answers, one per mode):

      mode="concise"  (default) — gate_and_retrieve()'s dynamic gate: best-of-
                        `best_of` cheap pipeline reformulations first (see
                        gate_and_retrieve's docstring), escalates only when
                        that winning result's confidence is still low.
                        "Doesn't drift" — stays tight unless the query
                        actually needs more.
      mode="detailed"           — retrieve_detailed(): always the fullest
                        pool (merged_all), no confidence check, no best-of-N
                        reformulation. Thorough by default, at merged_all's
                        one measured cost (Faithfulness 0.895, its weakest
                        metric).

    best_of (default 3): forwarded to gate_and_retrieve() for mode="concise"
    only — pass 0 to skip reformulation and use a single retrieve() call.
    Ignored for mode="detailed" (see gate_and_retrieve's docstring for why).

    prefer_method, if given, overrides both and forces a specific method —
    unchanged behavior, still bypasses caching-by-mode distinction (cached
    under "concise"'s key, since forcing a method isn't really either mode).

    Cache hit -> instant return, no LLM call at all. Cache miss -> retrieval
    per the selected mode + generation — NOT auto-written back to cache;
    seeding is a deliberate, separate step (build_cache_preset.py) so the
    demo's cached/uncached split stays exactly as curated.
    """
    resolved_corpus_id = corpus_id or retriever._classify_corpus_target(query, active_patient_corpus_id)

    t0 = time.time()
    cached = get_cached(query, mode, resolved_corpus_id)
    if cached:
        return {**cached, "from_cache": True, "latency_s": time.time() - t0}

    if prefer_method:
        method = prefer_method
        result = METHOD_FNS[method](query)  # NOTE: not corpus-aware — deliberately unchanged, see METHOD_FNS comment
    elif mode == "detailed":
        result = retrieve_detailed(query, corpus_id=resolved_corpus_id, active_patient_corpus_id=active_patient_corpus_id, original_query=original_query)
        method = result["_gated_method"]
    else:
        result = gate_and_retrieve(query, best_of=best_of, corpus_id=resolved_corpus_id, active_patient_corpus_id=active_patient_corpus_id, original_query=original_query)
        method = result["_gated_method"]

    answer = _generate_answer(query, result)
    return {
        "query": query, "method": method, "mode": mode, "corpus_id": resolved_corpus_id, "answer": answer,
        "confidence": result.get("_gated_confidence"),
        # Passthrough only — result["chunks"] is already the exact list
        # retriever.retrieve()/retrieve_merged()/retrieve_merged_all() produced
        # for whichever method the gate actually picked; no new retrieval call,
        # no re-ranking, nothing recomputed. Cached answers (from_cache=True,
        # above) don't carry this — caching is seeded separately by
        # build_cache_preset.py, which doesn't store chunks today.
        "chunks": result.get("chunks", []),
        "from_cache": False, "latency_s": time.time() - t0,
    }
