"""
stage2_config.py
─────────────────
Stage-2-only constants — deliberately NOT named "config.py". Stage 1's
main.py/run_tail.py and everything they import (ingest.py, rectifier.py,
delta_analyzer.py, topic_summarizer.py, ...) do `import config` expecting
Stage 1's own config.py to resolve. Since Stage 2 scripts put Stage 1's
directory at the front of sys.path so those imports keep working, a second
module also named "config.py" living in Stage 2/ would create a name
collision the moment anything imports it first — sys.modules caches modules
by name, not path, so whichever "config" loads first wins for the rest of
the process. This file exists to hold Stage-2-specific values without ever
touching that name.
"""

import os
import sys
from pathlib import Path

STAGE2_DIR   = Path(__file__).parent
STAGE1_DIR   = STAGE2_DIR.parent / "Stage 1"
RETRIEVAL_DIR = STAGE2_DIR.parent / "retrieval_layer"
# The 13 clinical guideline PDFs were already processed into Stage 1's own
# "default" data/output/ before this corpus_id split existed — reuse that
# directly rather than a separate default_clinical/data_guidelines build.
GUIDELINES_OUTPUT_DIR = STAGE1_DIR / "data" / "output"

# Replaced the offline SentenceTransformer+cosine-argmax match (and its
# GUIDELINE_MATCH_THRESHOLD/guideline_topic_embeddings.pkl) with a live,
# request-time lookup through retrieval_layer's own router+reranker — see
# retrieval_layer/cross_corpus.py and cross_corpus_cli.py. Top-K, not a
# hard similarity floor: the old 0.70/0.62 cosine threshold was calibrated
# against a different embedder and doesn't port to the cross-encoder's
# rerank_score scale (unbounded, real-data range observed this session was
# roughly -11 to +2.4) — cross_corpus.lookup_guideline_topics()'s min_score
# stays unset (no floor) until a real floor is calibrated from observed
# score distributions.
GUIDELINE_MATCH_TOP_K = 3
CROSS_CORPUS_CLI_PATH = RETRIEVAL_DIR / "cross_corpus_cli.py"
FUSION_WORKER_PATH    = STAGE2_DIR / "fusion_worker.py"


def _default_env(key: str, value: str) -> None:
    os.environ.setdefault(key, value)


def resolve_guideline_profile_path() -> str:
    """
    The guideline profile is cached per type_key under
    GUIDELINES_OUTPUT_DIR/profiles/<type_key>.json. Multiple type_keys can
    exist if the 13 guideline PDFs span more than one document family —
    picks the first one found. For a specific type_key, set
    STAGE2_FORCED_PROFILE_PATH yourself before running instead of relying on
    this default.
    """
    profiles_dir = GUIDELINES_OUTPUT_DIR / "profiles"
    if not profiles_dir.exists():
        return ""
    candidates = sorted(profiles_dir.glob("*.json"))
    return str(candidates[0]) if candidates else ""


def setup_patient_env(patient_id: str) -> None:
    """
    Sets STAGE1_PDF_DIR/STAGE1_OUTPUT_DIR/CHROMA_DIR_OVERRIDE/
    INDEX_DIR_OVERRIDE/STAGE2_FORCED_PROFILE_PATH for this patient_id if not
    already set by the caller (e.g. a future api_server.py job runner
    passing env= per corpus_id). Must run before `import config` anywhere —
    Stage 1's config.py resolves these once, at import time. Shared by
    Stage 2/main.py and Stage 2/run_tail.py — they run as separate process
    invocations, so each needs to call this itself.
    """
    patient_dir = STAGE2_DIR / "data" / patient_id
    _default_env("STAGE1_PDF_DIR", str(patient_dir / "pdfs"))
    _default_env("STAGE1_OUTPUT_DIR", str(patient_dir / "output"))
    _default_env("CHROMA_DIR_OVERRIDE", str(patient_dir / "chroma_db"))
    _default_env("INDEX_DIR_OVERRIDE", str(patient_dir / "index"))
    forced_profile = resolve_guideline_profile_path()
    if forced_profile:
        _default_env("STAGE2_FORCED_PROFILE_PATH", forced_profile)
    else:
        print(
            "WARNING: no guideline profile found under "
            f"{GUIDELINES_OUTPUT_DIR / 'profiles'} — process the guidelines "
            "corpus first (POST /api/process {\"corpus_id\": \"guidelines\"}, "
            "or the 'Build Guidelines KB' button), or set "
            "STAGE2_FORCED_PROFILE_PATH manually. Proceeding without a "
            "forced profile means this patient run would build its own "
            "(wrong per the design) — aborting.",
            file=sys.stderr,
        )
        sys.exit(1)


def put_stage1_first_on_path() -> None:
    """
    Stage 1's directory must take priority over Stage 2's on sys.path so
    that `import main`/`import run_tail`/`import config`/etc. resolve to
    Stage 1's modules, not a self-import of the Stage 2 file of the same
    name currently running.
    """
    sys.path.insert(0, str(STAGE1_DIR))
