"""
cross_corpus_cli.py
─────────────────────
Subprocess boundary letting Stage 1/Stage 2-context processes reach
cross_corpus.py's live guideline lookup without importing retrieval_layer's
modules in-process.

Two independent, verified reasons that in-process import is unsafe from a
Stage 2 pipeline/subprocess:

  1. `config` module-name collision — retrieval_layer/config.py and
     Stage 1/config.py are both module name "config" with disjoint
     attributes. Stage 2 already pins sys.modules['config'] to Stage 1's
     version (stage2_config.put_stage1_first_on_path()); any `import router`
     from that process would resolve router.py's own `import config` to the
     WRONG object already cached in sys.modules and crash on missing
     attributes (RERANKER_MODEL, TOP_K_FINAL, HNSW_EF_QUERY, ...).
  2. Env-override poisoning — even bypassing #1, corpus_registry's
     "guidelines" entry resolves through config.CHROMA_DIR/INDEX_DIR, which
     read CHROMA_DIR_OVERRIDE/INDEX_DIR_OVERRIDE — env vars Stage 2's
     setup_patient_env() already points at the PATIENT's own dirs. A
     same-process get_router("guidelines") would resolve to the patient
     corpus, not the guideline KB.

So: run as a fresh subprocess (mirrors the already-proven pattern in
Stage 1/run_tail.py's step_build_index()/step_chroma(), which already runs
`subprocess.run([sys.executable, ...], cwd=RETRIEVAL_DIR)` against this same
shared venv), with the six offending env vars explicitly stripped BEFORE
`config` (or anything importing it) is ever imported — order matters,
config.py reads these at import time.

Usage:
    python cross_corpus_cli.py --in queries.json --out results.json [--k 3] [--corpus-id guidelines]

queries.json:  [{"id": "<patient_topic_label>", "query": "<text>"}, ...]
results.json:  {"guideline_kb_version": "<hash>",
                 "results": {"<id>": [<lookup_guideline_topics() dict>, ...], ...}}

One process handles ALL queries in one call (batched) — pays the
SentenceTransformer/CrossEncoder/HNSW load cost once, not once per topic.
"""

import argparse
import json
import os
import sys

# Force CPU. This is a short-lived, on-demand subprocess, not a persistent
# server — it always runs alongside at least one already-warm, long-lived
# GPU process (the local llama.cpp fallback server, and/or retrieval_layer's
# own api_server.py process, which keeps its own reranker loaded on GPU for
# the life of the process). A second CUDA-resident model load from a fresh
# process routinely OOMs against those (observed directly: "CUDA out of
# memory... GPU 0 has a total capacity of 7.66 GiB of which 29.50 MiB is
# free" with the two long-lived processes alone already using ~7.4 GiB) —
# not a transient/rare contention edge case, a deterministic failure every
# time this CLI is invoked while either of those is running, which in
# practice is always. Must be set before any torch/sentence-transformers
# import — those libraries read this at import/device-selection time.
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Must happen BEFORE any import that could trigger `import config` —
# config.py (and anything importing it) reads these env vars at import time.
for _var in (
    "STAGE1_OUTPUT_DIR", "STAGE1_PDF_DIR",
    "CHROMA_DIR_OVERRIDE", "INDEX_DIR_OVERRIDE",
    "STAGE2_FORCED_PROFILE_PATH", "STAGE2_PATIENT_ID",
):
    os.environ.pop(_var, None)

# Also drop any stale Stage-1 `config`/module bindings a caller's own
# process might have left behind if this file were ever exec'd in-process
# by mistake (belt-and-suspenders — the intended usage is always a fresh
# `python cross_corpus_cli.py` subprocess, which has no such pollution).
for _mod in ("config", "router", "reranker", "chroma_store", "corpus_registry"):
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cross_corpus  # noqa: E402  (import deliberately deferred past the env strip above)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--corpus-id", default="guidelines")
    args = parser.parse_args()

    with open(args.in_path, "r", encoding="utf-8") as f:
        queries = json.load(f)

    results = {}
    for item in queries:
        qid = item["id"]
        query = item["query"]
        results[qid] = cross_corpus.lookup_guideline_topics(query, k=args.k, corpus_id=args.corpus_id)

    payload = {
        "guideline_kb_version": cross_corpus.guideline_kb_version(),
        "results": results,
    }
    with open(args.out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
