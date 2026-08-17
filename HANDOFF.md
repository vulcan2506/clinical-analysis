# Handoff — Patient–Guideline Semantic Enrichment Engine

## Status: DONE (all 6 tasks complete)

## What was delivered
- `Stage 2/guideline_grounding.py` — enrichment engine (parses 907-topic guideline hierarchy as semantic index; hybrid retrieval = local embeddings + `cross_corpus_cli` subprocess; LLM classify → deterministic context validation → extract → fuse → §22 records + additive nested-JSON patch). Run: `STAGE2_PATIENT_ID=dr-lalpath-labs <Stage1 venv python> guideline_grounding.py`
- `Stage 2/eval_grounding.py` — §34 harness, **6/6 pass, stable**. Run: same as above with `eval_grounding.py`
- `Stage 2/run_tail.py` — new step wired between `step_topic_summarizer()` and `step_hierarchy_summarizer()`.

## Bugs fixed along the way
- `_guideline_embed_text` wrong key (master_label vs label); `np.argsort` cast bug.
- Truncated LLM JSON → everything NO_MATCH. Fixed via salvage in `_extract_json_object` + classify max_tokens 1200→2600 (extract→1000, fuse→1200).
- **Pre-existing retrieval bug** (`retrieval_layer/cross_corpus.py`): `MIN_MATCH_SCORE=1.0` calibrated for old ms-marco reranker, but `RERANKER_MODEL` is now `NeuML/biomedbert-base-reranker` (sigmoid scores ~0.99). Every guideline lookup returned empty — silently killed ALL guideline fusion pipeline-wide. Recalibrated to `0.99` (genuine 0.998–1.0, boilerplate tops ~0.978).

## Guardrails added (anti-hallucination, §32)
- Admin guideline topics (author team, COI, funding, contact info…) filtered from candidates.
- Admin patient topics (directories/contact) forced NO_MATCH.
- Boilerplate-named patient topics now include clinical body in retrieval queries.

## Real-patient run (dr-lalpath-labs, 14 topics)
- 13/14 enriched (58 matches: 26 DIRECT / 23 PARTIAL / 9 CONTEXTUAL); Consultant Directory correctly NO_MATCH.
- Outputs → `Stage 2/data/dr-lalpath-labs/output/guideline_grounded_summaries/`; `enterprise_nested_topics.json` patched additively (originals intact).

## Known limitation
- Vitamin D Measurement Reference ↔ "glycemic management for bone health" (CONTEXTUAL) — borderline lab-reference over-match via diabetes context. Accept or force NO_MATCH as you prefer.

## Next steps / open questions
- Recalibrate `MIN_MATCH_SCORE` further as more real-query scores accumulate (single knob in `cross_corpus.py`).
- Consider a LLM-confidence floor or subject-conservatism instruction if Vitamin D-style over-matches recur.
- Verified LLM backend: Stage 1 `.env` (Mistral→OpenRouter→Groq→local llama). API rate limits (429) auto-fallback works.

## Key commands
- Engine: `cd "Stage 2" && STAGE2_PATIENT_ID=dr-lalpath-labs <venv python> guideline_grounding.py [--limit N] [--no-llm]`
- Eval: `cd "Stage 2" && STAGE2_PATIENT_ID=dr-lalpath-labs <venv python> eval_grounding.py`
- Venv python: `"Stage 1/venv/bin/python"`