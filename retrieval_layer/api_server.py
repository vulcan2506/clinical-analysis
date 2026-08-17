"""
api_server.py
──────────────
Thin FastAPI layer over the existing retrieval_layer + Stage 1 backend, for
the Next.js frontend. Every handler is a direct call into existing modules
(redis_cache, retriever, config, llm_client) or a subprocess invocation of an
existing script (main.py, run_tail.py) — no retrieval, gating, reranking, OCR,
delta-analysis, or citation-selection logic lives in this file.

Run:
    cd retrieval_layer && uvicorn api_server:app --reload --port 8000

CORS is open to the local Next.js dev server only (http://localhost:3000).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import chroma_store
import config
import cross_corpus
import llm_client
import redis_cache
import retriever
import router
import session as session_module

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Clinical Knowledge API")

# ALLOWED_ORIGINS: comma-separated list, e.g. "https://your-app.vercel.app,http://localhost:3000".
# Defaults to local dev only — set this env var on the deployed backend host
# to the deployed Vercel frontend URL (and localhost, if you also test locally
# against the deployed backend).
_allowed_origins = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STAGE1_DIR = config.STAGE1_DIR
STAGE2_DIR = STAGE1_DIR.parent / "Stage 2"
PDF_DIR    = STAGE1_DIR / "data" / "pdfs"
OUTPUT_DIR = config.STAGE1_OUTPUT
# "guidelines" is an ALIAS onto Stage 1's existing "default" storage — the 13
# clinical guideline PDFs were already processed there (data/pdfs, data/
# output) before this corpus_id split existed. NOT a separate
# default_clinical/data_guidelines build target — see corpus_registry.py's
# matching alias for the chroma/index side of this.
GUIDELINES_PDF_DIR    = PDF_DIR
GUIDELINES_OUTPUT_DIR = OUTPUT_DIR
PATIENTS_DIR = STAGE2_DIR / "data"

# corpus_id becomes part of filesystem paths below (upload/process/reset/
# knowledge-explorer) — a client-supplied one must never contain path
# separators or traversal sequences.
_CORPUS_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_patient_corpus_id(corpus_id: str) -> None:
    if corpus_id in ("default", "guidelines"):
        raise HTTPException(400, f"'{corpus_id}' is not a patient corpus id.")
    if not _CORPUS_ID_RE.match(corpus_id):
        raise HTTPException(400, "Invalid corpus_id.")


def _patient_dir(corpus_id: str) -> Path:
    return PATIENTS_DIR / corpus_id


def _patient_chroma_index_dirs(corpus_id: str) -> "tuple[Path, Path]":
    # Mirrors Stage 2/stage2_config.py:setup_patient_env()'s own formula —
    # must stay in lockstep with it since that's what the patient subprocess
    # actually builds against.
    patient_dir = _patient_dir(corpus_id)
    return patient_dir / "chroma_db", patient_dir / "index"


def _register_existing_patient_corpora() -> None:
    """
    corpus_registry is in-process-only memory, normally populated as the
    final step of /api/process's patient branch (_run_process_job). A live
    server restart — or a patient corpus rebuilt via a direct script that
    bypasses /api/process entirely, which happens during development —
    loses that registration even though the corpus's chroma_db/index are
    fully valid and already built on disk. Without this, router.get_router()/
    chroma_store.get_store() raise "Unknown corpus_id" for a corpus that's
    completely usable, and the only fix was a full (expensive) reprocess
    just to restore in-memory routing state. Scanning once at process start
    (re-run automatically on every --reload) makes every already-processed
    patient corpus immediately queryable again for free.
    """
    if not PATIENTS_DIR.exists():
        return
    import corpus_registry
    for patient_dir in sorted(PATIENTS_DIR.iterdir()):
        if not patient_dir.is_dir():
            continue
        chroma_dir, index_dir = _patient_chroma_index_dirs(patient_dir.name)
        if chroma_dir.exists() and index_dir.exists() and any(index_dir.iterdir()):
            corpus_registry.register(patient_dir.name, chroma_dir, index_dir)
            log.info(f"Auto-registered existing patient corpus '{patient_dir.name}' on startup")


_register_existing_patient_corpora()


def _stage2_config():
    # Stage 2/ isn't on sys.path by default (api_server.py runs with cwd=
    # retrieval_layer/) — this is a lightweight, side-effect-free module
    # (just path constants + pure helper functions), safe to import directly
    # without also needing Stage 1 on sys.path.
    if str(STAGE2_DIR) not in sys.path:
        sys.path.insert(0, str(STAGE2_DIR))
    import stage2_config
    return stage2_config

# ── Conversation sessions ────────────────────────────────────────────────────
# One ConversationSession per frontend chat thread (session.py — already used
# by cli.py's default REPL loop for every non-`--session` mode, including the
# gated/cached path this endpoint wraps). Follow-ups like "explain that in
# more detail" get rewritten into a standalone query BEFORE retrieval, using
# the trimmed conversation window — without this, a pronoun-only follow-up is
# retrieved on its own literal (near-contentless) text, pulls in unrelated
# chunks, and the LLM ends up answering ungrounded — reads as hallucination.
# Keyed by the frontend's per-thread session_id; a request with no session_id
# stays fully stateless (unchanged prior behavior).
_sessions: Dict[str, session_module.ConversationSession] = {}
_sessions_lock = threading.Lock()


def _get_session(session_id: str) -> session_module.ConversationSession:
    with _sessions_lock:
        sess = _sessions.get(session_id)
        if sess is None:
            sess = session_module.ConversationSession()
            _sessions[session_id] = sess
        return sess


# Which Stage 2 (fused) patient corpus_id, if any, is active for a given
# session_id — consulted by retriever._classify_corpus_target() via
# ChatRequest.corpus_id resolution below when the request doesn't pass an
# explicit override. Single-active-patient-corpus-per-session is the
# deliberate scope boundary for now, not a full multi-tenant patient
# database. A request with no session_id has no active patient corpus and
# always resolves to "guidelines" unless it passes corpus_id explicitly.
_active_patient_corpus: Dict[str, str] = {}
_active_patient_corpus_lock = threading.Lock()


def set_active_patient_corpus(session_id: str, corpus_id: str) -> None:
    with _active_patient_corpus_lock:
        _active_patient_corpus[session_id] = corpus_id


def _get_active_patient_corpus(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    return _active_patient_corpus.get(session_id)


@app.get("/")
def root() -> Dict:
    # HF Spaces' own readiness probe hits "/" before flipping public edge
    # routing live — without a route here it 404s, and the Space can stay
    # stuck showing HF's placeholder page even once the container is
    # genuinely up and /api/health works internally.
    return {"status": "ok", "service": "Clinical Knowledge API"}


@app.get("/api/health")
def health() -> Dict:
    return {"status": "ok"}


# ── /api/chat ────────────────────────────────────────────────────────────────
# Mirrors cli.py's real branching exactly (see cli.py's REPL loop):
#   - forcing `intent` only ever has effect on the raw/diagnostic path
#     (redis_cache.answer_query() never receives an intent — confirmed by
#     reading cli.py's gated-mode branch, which only forwards mode/best_of).
#     So selecting a non-"auto" intent here switches to raw mode automatically,
#     same as cli.py's `--intent` implicitly requiring `--raw` to have effect.
#   - raw + best_of together uses retrieve_best_of_n (cli.py's
#     `elif turn_raw and turn_best_of:` branch) — best-of still applies in
#     diagnostic mode, mode/best_of are just otherwise unused there.

class ChatRequest(BaseModel):
    query: str
    mode: str = "concise"           # "concise" | "detailed" — redis_cache.answer_query's two modes
    best_of: int = 3                # reformulation count — gated path (mode=concise) or raw path, both real
    intent: Optional[str] = None    # forcing this switches to the raw/diagnostic path (see above)
    version: Optional[str] = None   # only meaningful on the raw/diagnostic path
    raw: bool = False                # explicit opt-in to the raw/diagnostic path with no intent forced
    session_id: Optional[str] = None  # frontend chat-thread id — omit for stateless single-shot calls
    corpus_id: Optional[str] = None   # explicit override — "guidelines" | a Stage 2 patient corpus_id.
                                       # None -> resolved via retriever._classify_corpus_target() against
                                       # this session's active patient corpus (set_active_patient_corpus()),
                                       # if any.
    active_patient_corpus_id: Optional[str] = None  # frontend sends the selected patient corpus ID
                                                     # directly — enables cross-corpus retrieval when
                                                     # the query contains cross-corpus signals.


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict:
    if not req.query.strip():
        raise HTTPException(400, "query is required")

    sess = _get_session(req.session_id) if req.session_id else None
    if sess is not None:
        standalone, was_rewritten = sess.prepare_turn(req.query)
    else:
        standalone, was_rewritten = req.query, False

    active_patient_corpus_id = req.active_patient_corpus_id or _get_active_patient_corpus(req.session_id)

    if req.raw or req.intent:
        t0 = time.time()
        if req.best_of and req.best_of > 1:
            result = retriever.retrieve_best_of_n(
                standalone, n=req.best_of, intent=req.intent, version=req.version,
                corpus_id=req.corpus_id, active_patient_corpus_id=active_patient_corpus_id,
            )
        else:
            result = retriever.retrieve(
                standalone, intent=req.intent, version=req.version,
                corpus_id=req.corpus_id, active_patient_corpus_id=active_patient_corpus_id,
            )
        if sess is not None:
            sess.record_turn(req.query, result)  # no answer generated on the raw path — nothing to add_assistant_turn
        return {
            "query":            req.query,
            "standalone_query": standalone,
            "was_rewritten":    was_rewritten,
            "method":      result.get("intent", "specific"),
            "mode":        "raw",
            "answer":      None,
            "confidence":  retriever._top_rerank_score(result),
            "chunks":      result.get("chunks", []),
            "corpus_id":   result.get("corpus_id"),
            "from_cache":  False,
            "latency_s":   time.time() - t0,
        }

    if req.mode not in ("concise", "detailed"):
        raise HTTPException(400, f"mode must be 'concise' or 'detailed', got {req.mode!r}")

    try:
        result = redis_cache.answer_query(
            standalone, mode=req.mode, best_of=req.best_of,
            corpus_id=req.corpus_id, active_patient_corpus_id=active_patient_corpus_id,
            original_query=req.query,
        )
    except Exception as e:
        log.exception("chat request failed")
        raise HTTPException(500, str(e))

    if sess is not None:
        sess.record_turn(req.query, result)
        if result.get("answer"):
            sess.add_assistant_turn(result["answer"])

    return {**result, "query": req.query, "standalone_query": standalone, "was_rewritten": was_rewritten}


# ── /api/settings ────────────────────────────────────────────────────────────
# BYOK (per-user key entry) was removed (2026-07-14) — the deployed server now
# always uses the operator's own MISTRAL_API_KEY from Stage 1/.env for every
# visitor, so there's nothing for a client to configure here beyond status.

@app.get("/api/settings/status")
def settings_status() -> Dict:
    return {
        "backend":  config.LLM_BACKEND,
        "model":    config.MISTRAL_CHAT_MODEL,
        "has_key":  bool(config.MISTRAL_API_KEY),
    }


# ── /api/corpora ─────────────────────────────────────────────────────────────
# Lists the permanent "guidelines" reference KB (an alias onto Stage 1's own
# already-processed data/pdfs -> data/output — see GUIDELINES_PDF_DIR/
# GUIDELINES_OUTPUT_DIR above) and every per-customer Stage 2 patient corpus
# (Stage 2/data/<id>/), so the frontend's left/right Knowledge panels can show
# real state instead of guessing from job status alone.

def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug or "customer"


def _split_source_docs(value) -> List[str]:
    """Normalize topic_registry.csv's source_docs cell (a possibly multiline
    doc-name list) into a clean list of filenames."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if not value:
        return []
    parts = re.split(r"[\n,;|]+", str(value))
    return [p.strip() for p in parts if p.strip()]


@app.get("/api/corpora")
def list_corpora() -> Dict:
    guidelines = {
        "id": "guidelines",
        "label": "Clinical Guidelines",
        "pdf_count": len(list(GUIDELINES_PDF_DIR.glob("*.pdf"))) if GUIDELINES_PDF_DIR.exists() else 0,
        "built": (GUIDELINES_OUTPUT_DIR / "enterprise_nested_topics.json").exists(),
        # No "embeddings_built" field anymore — cross-corpus lookups are live
        # (retrieval_layer/cross_corpus.py), nothing to precompute beyond
        # what "built" already reflects.
    }

    patients: List[Dict] = []
    if PATIENTS_DIR.exists():
        for patient_dir in sorted(PATIENTS_DIR.iterdir()):
            if not patient_dir.is_dir():
                continue
            meta_path = patient_dir / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            pdfs_dir = patient_dir / "pdfs"
            patients.append({
                "id": patient_dir.name,
                "label": meta.get("label", patient_dir.name),
                "pdf_count": len(list(pdfs_dir.glob("*.pdf"))) if pdfs_dir.exists() else 0,
                "built": (patient_dir / "output" / "enterprise_nested_topics.json").exists(),
            })

    return {"guidelines": guidelines, "patients": patients}


class PatientCorpusCreateRequest(BaseModel):
    label: str


@app.post("/api/corpora/patients")
def create_patient_corpus(req: PatientCorpusCreateRequest) -> Dict:
    label = req.label.strip()
    if not label:
        raise HTTPException(400, "label is required")

    corpus_id = _slugify(label)
    if _patient_dir(corpus_id).exists():
        corpus_id = f"{corpus_id}-{uuid.uuid4().hex[:6]}"

    patient_dir = _patient_dir(corpus_id)
    (patient_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    (patient_dir / "output").mkdir(parents=True, exist_ok=True)
    (patient_dir / "meta.json").write_text(json.dumps({"label": label}), encoding="utf-8")
    return {"id": corpus_id, "label": label}


@app.post("/api/corpora/patients/{corpus_id}/register")
def register_patient_corpus(corpus_id: str) -> Dict:
    """
    Manual escape hatch alongside _register_existing_patient_corpora()'s
    startup scan — for when a patient corpus is rebuilt (e.g. via a direct
    script) WHILE the server is already running and never restarts/reloads,
    so the startup scan never gets another chance to run. Idempotent, cheap
    (no LLM/embedding cost) — just points router.get_router()/
    chroma_store.get_store() at the corpus's already-built directories.
    """
    _validate_patient_corpus_id(corpus_id)
    patient_dir = _patient_dir(corpus_id)
    if not patient_dir.exists():
        raise HTTPException(404, f"Unknown customer corpus '{corpus_id}'.")
    chroma_dir, index_dir = _patient_chroma_index_dirs(corpus_id)
    if not chroma_dir.exists() or not index_dir.exists() or not any(index_dir.iterdir()):
        raise HTTPException(400, f"Corpus '{corpus_id}' hasn't been fully processed yet — run Process first.")

    import corpus_registry
    corpus_registry.register(corpus_id, chroma_dir, index_dir)
    router.reset_router(corpus_id)
    chroma_store.reset_store(corpus_id)
    return {"status": "ok", "corpus_id": corpus_id}

    return {"id": corpus_id, "label": label}


# ── /api/upload ──────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), corpus_id: str = "default") -> Dict:
    safe_name = os.path.basename(file.filename or "")
    if not safe_name or safe_name in (".", "..") or not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF uploads are supported today.")

    if corpus_id == "default":
        target_dir = PDF_DIR
    else:
        _validate_patient_corpus_id(corpus_id)
        patient_dir = _patient_dir(corpus_id)
        if not patient_dir.exists():
            raise HTTPException(404, f"Unknown customer corpus '{corpus_id}' — create it first.")
        target_dir = patient_dir / "pdfs"

    target_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    (target_dir / safe_name).write_bytes(content)
    return {"filename": safe_name, "size_bytes": len(content)}


# ── /api/reset ───────────────────────────────────────────────────────────────
# Clears the demo/previous corpus (source PDFs, Stage 1 output, chroma_db,
# index) so a client can process their own PDF set from a clean slate instead
# of it merging with whatever was there before — /api/process has no
# incremental mode, so leftover old PDFs would otherwise get reprocessed
# alongside the new ones.

class ResetRequest(BaseModel):
    confirm: bool = False
    corpus_id: str = "default"   # "default" (today's single live corpus) or a Stage 2 patient corpus_id.
                                  # "guidelines" is refused below — that KB is permanent, never reset via this endpoint.


def _clear_dir_contents(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


@app.post("/api/reset")
def reset_corpus(req: ResetRequest) -> Dict:
    if not req.confirm:
        raise HTTPException(400, "Set confirm=true to clear the corpus — this deletes all "
                                  "processed PDFs, generated output, and the vector store.")

    if req.corpus_id == "guidelines":
        raise HTTPException(400, "The 'guidelines' corpus is permanent and cannot be reset via this endpoint.")

    if req.corpus_id == "default":
        _clear_dir_contents(PDF_DIR)
        _clear_dir_contents(OUTPUT_DIR)
        _clear_dir_contents(config.CHROMA_DIR)
        _clear_dir_contents(config.INDEX_DIR)
    else:
        _validate_patient_corpus_id(req.corpus_id)
        patient_dir = _patient_dir(req.corpus_id)
        if not patient_dir.exists():
            raise HTTPException(404, f"Unknown customer corpus '{req.corpus_id}'.")
        _clear_dir_contents(patient_dir / "pdfs")
        _clear_dir_contents(patient_dir / "output")
        # Formula-derived, not corpus_registry.resolve() — a corpus that was
        # uploaded-to but never successfully processed was never register()'d
        # in-memory, and reset must still work for that case.
        chroma_dir, index_dir = _patient_chroma_index_dirs(req.corpus_id)
        _clear_dir_contents(chroma_dir)
        _clear_dir_contents(index_dir)

    router.reset_router(req.corpus_id)
    chroma_store.reset_store(req.corpus_id)
    with _sessions_lock:
        _sessions.clear()
    if req.corpus_id != "default":
        with _active_patient_corpus_lock:
            stale = [sid for sid, cid in _active_patient_corpus.items() if cid == req.corpus_id]
            for sid in stale:
                del _active_patient_corpus[sid]

    return {"status": "ok", "message": f"Corpus '{req.corpus_id}' cleared. Upload new PDFs and run Process to build a fresh one."}


# ── /api/process ─────────────────────────────────────────────────────────────
# Runs Stage 1's own main.py + run_tail.py --skip-eval as subprocesses — this
# reprocesses the ENTIRE data/pdfs/ corpus (main.py has no incremental/single-
# file mode), not just the file just uploaded. See Known Gaps in the plan.

_jobs: Dict[str, Dict] = {}
_jobs_lock = threading.Lock()
_STAGE_RE = re.compile(r"\[\d+/\d+\][^\n]*")


def _tail_log_into_job(proc: subprocess.Popen, log_path: Path, job_id: str, poll_interval: float = 1.0) -> None:
    while proc.poll() is None:
        time.sleep(poll_interval)
        _update_job_from_log(log_path, job_id)
    _update_job_from_log(log_path, job_id)


def _update_job_from_log(log_path: Path, job_id: str) -> None:
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = _STAGE_RE.findall(text)
    with _jobs_lock:
        if job_id not in _jobs:
            return
        if matches:
            _jobs[job_id]["stage"] = matches[-1].strip()
        _jobs[job_id]["log_tail"] = text[-4000:]


def _run_process_job(
    job_id: str,
    corpus_id: str = "default",
    env_overrides: Optional[Dict[str, str]] = None,
    work_dir: Optional[Path] = None,
    steps: Optional[List["tuple[List[str], str]"]] = None,
) -> None:
    """
    corpus_id/env_overrides/work_dir/steps default to today's exact behavior
    (corpus_id="default", env_overrides=None -> subprocess inherits this
    process's own environment unchanged, work_dir=STAGE1_DIR, steps=main.py
    then run_tail.py --skip-eval) — the "default" branch of /api/process below
    still calls this with no overrides at all.

    env_overrides, when given, is merged into the subprocess environment —
    this is the fix for the confirmed gap where STAGE1_PDF_DIR/
    STAGE1_OUTPUT_DIR/CHROMA_DIR_OVERRIDE/INDEX_DIR_OVERRIDE existed in
    config.py but were never actually set for a subprocess run, so per-
    corpus isolation never took effect end-to-end. Used by the "guidelines"
    and patient-id branches of /api/process below.

    steps is a list of (argv_tail, stage_label) run sequentially with
    sys.executable prepended and cwd=work_dir — lets a patient corpus run
    Stage 2's main.py/run_tail.py (different args, different cwd) instead of
    Stage 1's.
    """
    cwd = work_dir or STAGE1_DIR
    job_env = {**os.environ, **env_overrides} if env_overrides else None
    steps = steps or [
        (["main.py"], "Starting main.py (ingestion)…"),
        (["run_tail.py", "--skip-eval"], "Starting run_tail.py (taxonomy, delta, indexing)…"),
    ]

    log_path = OUTPUT_DIR / f"process_{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "stage": steps[0][1], "log_tail": ""}

    try:
        for i, (argv, stage_label) in enumerate(steps):
            with _jobs_lock:
                _jobs[job_id]["stage"] = stage_label
            mode = "w" if i == 0 else "a"
            with open(log_path, mode, encoding="utf-8") as logf:
                proc = subprocess.Popen(
                    [sys.executable, *argv], cwd=str(cwd),
                    stdout=logf, stderr=subprocess.STDOUT, env=job_env,
                )
                _tail_log_into_job(proc, log_path, job_id)
                if proc.returncode != 0:
                    raise RuntimeError(f"{argv[0]} exited with code {proc.returncode}")

        # router/chroma_store cache their index/store in memory for the life
        # of this process (see their double-checked-locking singletons) —
        # without dropping them here, a long-lived api_server keeps serving
        # the OLD corpus after a successful reprocess.
        router.reset_router(corpus_id)
        chroma_store.reset_store(corpus_id)
        if corpus_id == "guidelines":
            # cross_corpus.py's reverse-map/registry-rows/kb-version
            # singletons are keyed off the guideline topic_registry.csv —
            # drop them so a reprocessed guideline KB isn't served under a
            # stale version hash (which would also keep old xcorp Redis
            # cache entries alive under a key that no longer matches
            # reality — reset_cache() forces a fresh guideline_kb_version()
            # on the next lookup, which naturally changes the cache key).
            cross_corpus.reset_cache()

        if corpus_id not in ("default", "guidelines"):
            # Patient corpora aren't pre-seeded in corpus_registry.py the way
            # "guidelines" is — register so router.get_router()/chroma_store.
            # get_store() can find this corpus_id's chroma/index dirs at all.
            import corpus_registry
            chroma_dir, index_dir = _patient_chroma_index_dirs(corpus_id)
            corpus_registry.register(corpus_id, chroma_dir, index_dir)

        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["stage"] = "Complete"
    except Exception as e:
        log.exception(f"process job {job_id} failed")
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["stage"] = str(e)


class ProcessRequest(BaseModel):
    corpus_id: str = "default"   # "default" | "guidelines" | a Stage 2 patient corpus_id


@app.post("/api/process")
def process(req: ProcessRequest = ProcessRequest()) -> Dict:
    job_id = uuid.uuid4().hex[:12]

    if req.corpus_id == "default":
        kwargs = {}

    elif req.corpus_id == "guidelines":
        # "guidelines" is an alias onto Stage 1's own "default" PDF/output/
        # chroma/index storage (already built, pre-dating this corpus_id
        # split — see GUIDELINES_PDF_DIR/GUIDELINES_OUTPUT_DIR and
        # corpus_registry.py's matching alias) — so no env overrides are
        # needed here, same as the "default" branch above. No extra
        # embeddings-index step anymore — cross-corpus lookups
        # (cross_corpus.py) are live against router+reranker, nothing to
        # precompute here beyond what run_tail.py already builds.
        kwargs = {}

    else:
        _validate_patient_corpus_id(req.corpus_id)
        patient_dir = _patient_dir(req.corpus_id)
        if not patient_dir.exists():
            raise HTTPException(404, f"Unknown customer corpus '{req.corpus_id}' — create it first.")

        s2cfg = _stage2_config()
        if not s2cfg.resolve_guideline_profile_path():
            raise HTTPException(
                400,
                "The Clinical Guidelines KB hasn't been built yet — process the "
                "guidelines corpus first, then reprocess this customer KB.",
            )

        kwargs = {
            "env_overrides": {"STAGE2_PATIENT_ID": req.corpus_id},
            "work_dir": STAGE2_DIR,
            "steps": [
                (["main.py"], "Starting Stage 2 main.py (ingestion)…"),
                (["run_tail.py"], "Starting Stage 2 run_tail.py (taxonomy, delta, indexing)…"),
            ],
        }

    threading.Thread(target=_run_process_job, args=(job_id, req.corpus_id), kwargs=kwargs, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/process/{job_id}")
def process_status(job_id: str) -> Dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job_id")
    return {"job_id": job_id, **job}


# ── /api/corpora/patients/{corpus_id}/topics/{topic_slug} ──────────────────────
# Replaces the deleted Stage 2/guideline_fusion.py splice mechanism. Neither
# the patient KB nor the guideline KB is ever written to by either endpoint
# below — both are request-time, live cross-corpus lookups (cross_corpus.py's
# router+reranker, same mechanism the chat two-hop uses), cached only as a
# transient derived Redis entry keyed on guideline_kb_version, never as a
# file under data/output/. See the design plan for the full rationale.

def _find_patient_topic_row(corpus_id: str, topic_slug: str) -> dict:
    """
    topic_registry.csv has no slug column (only master_label) — resolve by
    slugifying every row's master_label with the SAME _slugify() already
    used for corpus labels, so the URL-facing id is stable and human-typeable
    without requiring the caller to exactly reproduce a label's casing/
    punctuation.
    """
    _validate_patient_corpus_id(corpus_id)
    patient_dir = _patient_dir(corpus_id)
    registry_path = patient_dir / "output" / "topic_registry.csv"
    if not registry_path.exists():
        raise HTTPException(404, f"Unknown or unprocessed customer corpus '{corpus_id}'.")

    import pandas as pd
    df = pd.read_csv(registry_path)
    for _, row in df.iterrows():
        label = row.get("master_label", "")
        if isinstance(label, str) and _slugify(label) == topic_slug:
            return row.to_dict()
    raise HTTPException(404, f"Unknown topic '{topic_slug}' in corpus '{corpus_id}'.")


# Original guideline summaries, verbatim — the knowledge artifact returned by
# /enhanced-summary. Matching is routing; the curated per-source summary files
# (Stage 1/data/output/topic_summaries/<source_dir>/<slug>.md) are consumed
# as-is, never regenerated/paraphrased. Same loader strategy as
# Stage 2/guideline_grounding.py::_load_guideline_summary_store().

_GUIDELINE_SUMMARY_STORE: Optional[Dict[str, List[dict]]] = None


def _load_guideline_summary_store() -> Dict[str, List[dict]]:
    global _GUIDELINE_SUMMARY_STORE
    if _GUIDELINE_SUMMARY_STORE is not None:
        return _GUIDELINE_SUMMARY_STORE
    store: Dict[str, List[dict]] = {}
    base = GUIDELINES_OUTPUT_DIR / "topic_summaries"
    if base.exists():
        for path in sorted(base.glob("*/*.md")):
            label, source, body = _parse_topic_summary_file(path)
            if not label:
                continue
            markdown = f"# {label}\n*Source: {source}*\n\n{body}"
            store.setdefault(label, []).append({"source_doc": source, "markdown": markdown})
    _GUIDELINE_SUMMARY_STORE = store
    return store


def _parse_topic_summary_file(path: Path):
    """Parse a dense per-topic summary file. Returns (label, source_doc, body).
    Format: '# <label>' then '*Source: <doc>*' (may span lines) then body.
    Mirrors Stage 2/guideline_grounding.py::_parse_patient_topic_file()."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    label = ""
    for ln in lines:
        if ln.startswith("# "):
            label = ln[2:].strip()
            break
    i = 0
    while i < len(lines) and not lines[i].strip().startswith("*Source:"):
        i += 1
    if i >= len(lines):
        return label, "", "\n".join(lines).strip()
    doc_lines: List[str] = []
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("*Source:"):
            ln = ln[len("*Source:"):].strip()
        doc_lines.append(ln)
        i += 1
        if ln.rstrip().endswith("*"):
            break
    source = " ".join(d.rstrip("*").strip() for d in doc_lines if d).strip()
    body = "\n".join(lines[i:]).strip()
    return label, source, body


def _original_summary_for(label: str) -> str:
    """Full original guideline topic summary (all source-specific sections),
    verbatim. Empty string when the label is unknown."""
    store = _load_guideline_summary_store()
    entries = store.get(label, [])
    if not entries:
        return ""
    return "\n\n---\n\n".join(e["markdown"] for e in entries)


def _offline_grounding_record(corpus_id: str, topic_slug: str) -> Optional[dict]:
    """Per-topic grounding record from the last offline Stage 2 run, if any.
    This is the authoritative source when present — it already carries the
    original summaries, match types and scores. None otherwise. Stage 2's
    pipeline slugs topic labels with underscores (see guideline_grounding.py::
    _slugify) while the URL-facing slug here uses hyphens — resolve the record
    through the registry label so the two conventions can't drift."""
    record = None
    try:
        row = _find_patient_topic_row(corpus_id, topic_slug)
        label = row.get("master_label", "")
        if label:
            pipeline_slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:80] or "untitled"
            record_path = _patient_dir(corpus_id) / "output" / "guideline_grounded_summaries" / f"{pipeline_slug}.json"
            if record_path.exists():
                record = json.loads(record_path.read_text(encoding="utf-8"))
    except HTTPException:
        return None
    except (json.JSONDecodeError, OSError):
        return None
    return record


@app.get("/api/corpora/patients/{corpus_id}/topics/{topic_slug}/enhanced-summary")
def enhanced_summary(corpus_id: str, topic_slug: str) -> Dict:
    """
    Returns this patient topic's own summary PLUS the matched guideline
    topics' ORIGINAL curated summaries — matching is routing, never rewriting
    (no LLM merge; original guideline summary text is returned verbatim with
    match provenance). Prefers the authoritative offline Stage 2 grounding
    record for the topic when one exists; otherwise falls back to a live
    cross-corpus lookup (cross_corpus.py) with original summaries resolved
    from the guideline summary store. Fully in-process — no subprocess needed.
    """
    row = _find_patient_topic_row(corpus_id, topic_slug)
    topic_label = row.get("master_label", topic_slug)
    patient_summary = row.get("grounded_summary") or row.get("summarized_description") or ""
    if not patient_summary or not isinstance(patient_summary, str):
        raise HTTPException(422, f"Topic '{topic_slug}' has no summary yet.")

    kb_version = cross_corpus.guideline_kb_version()
    cache_key = f"{corpus_id}:{topic_slug}:{kb_version}"
    cached = redis_cache.get_xcorp_cached("enhanced", cache_key, kb_version)
    if cached:
        return {**cached, "from_cache": True}

    offline = _offline_grounding_record(corpus_id, topic_slug)
    if offline is not None and offline.get("grounding"):
        matched = []
        for m in offline["grounding"].get("matched_guideline_topics", []):
            summary = m.get("summary") or _original_summary_for(m.get("master_label", ""))
            matched.append({
                "label": m.get("master_label", ""),
                "match_type": m.get("match_type", ""),
                "score": m.get("score"),
                "match_reason": m.get("match_reason", ""),
                "source_docs": m.get("source_docs", []),
                "summary": summary,
            })
        result = {
            "topic": topic_label,
            "patient_summary": patient_summary,
            "matched_guideline_topics": matched,
            "grounding_status": offline["grounding"].get("status", "NO_MATCH"),
            "from_offline": True,
        }
        redis_cache.set_xcorp_cached("enhanced", cache_key, kb_version, result)
        return {**result, "from_cache": False}

    matches = cross_corpus.lookup_guideline_topics(patient_summary, k=_stage2_config().GUIDELINE_MATCH_TOP_K)
    matched = [
        {
            "label": m["master_label"],
            "match_type": "",
            "score": m.get("score"),
            "match_reason": "",
            "source_docs": _split_source_docs(m.get("source_docs", "")),
            "summary": _original_summary_for(m["master_label"]),
        }
        for m in matches
    ]
    result = {
        "topic": topic_label,
        "patient_summary": patient_summary,
        "matched_guideline_topics": matched,
        "grounding_status": "DIRECT_MATCH" if matched else "NO_MATCH",
        "from_offline": False,
    }
    redis_cache.set_xcorp_cached("enhanced", cache_key, kb_version, result)
    return {**result, "from_cache": False}


@app.get("/api/corpora/patients/{corpus_id}/topics/{topic_slug}/guideline-conformance")
def guideline_conformance(corpus_id: str, topic_slug: str) -> Dict:
    """
    Delta conformance (Concordant/Deviates/Guideline Silent) + evolution
    cards for this patient topic against its matched guideline topics.
    Needs Stage 1's delta_analyzer/evolution_analyzer machinery, which
    cannot run in THIS process (see cross_corpus_cli.py's docstring for the
    config-collision reason) — shells out to Stage 2/fusion_worker.py on
    cache miss; that subprocess itself shells out again to
    cross_corpus_cli.py for the live guideline lookup.
    """
    row = _find_patient_topic_row(corpus_id, topic_slug)
    topic_label = row.get("master_label", topic_slug)

    kb_version = cross_corpus.guideline_kb_version()
    cached = redis_cache.get_xcorp_cached("conformance", f"{corpus_id}:{topic_slug}", kb_version)
    if cached:
        return {**cached, "from_cache": True}

    s2cfg = _stage2_config()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "result.json"
        proc = subprocess.run(
            [sys.executable, str(s2cfg.FUSION_WORKER_PATH),
             "--patient-corpus-id", corpus_id, "--topic", topic_label, "--out", str(out_path)],
            cwd=str(STAGE2_DIR), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            log.error(f"fusion_worker.py failed for {corpus_id}/{topic_label}: {proc.stderr[-2000:]}")
            raise HTTPException(500, "Guideline conformance analysis failed — see server logs.")
        result = json.loads(out_path.read_text(encoding="utf-8"))

    redis_cache.set_xcorp_cached("conformance", f"{corpus_id}:{topic_slug}", kb_version, result)
    return {**result, "from_cache": False}


# ── /api/knowledge ───────────────────────────────────────────────────────────
# Curated view of Stage 1/data/output/ — the real files discovered while
# planning this (not the illustrative names from the original spec).

_DISPLAY_NAMES = {
    "hierarchy_summaries":               "Knowledge Hierarchy",
    "topic_summaries":                   "Topic Insights",
    "delta_reports":                     "Delta Reports",
    "version_delta_report.md":           "Version Differences",
    "version_evolution_report.md":       "Version Evolution",
    "enterprise_nested_topics.json":     "Knowledge Structure",
    "parent_relationship_clusters.json": "Relationship Graph",
    "eval_report.md":                    "Evaluation Report",
}
_TOP_LEVEL_ALLOWLIST = set(_DISPLAY_NAMES)
_ALLOWED_EXTENSIONS  = {".md", ".json"}


def _display_name(name: str) -> str:
    return _DISPLAY_NAMES.get(name, name)


def _build_tree(path: Path, output_root: Path, top_level: bool = True) -> List[Dict]:
    entries = []
    if not path.exists():
        return entries
    for child in sorted(path.iterdir()):
        if top_level and child.name not in _TOP_LEVEL_ALLOWLIST:
            continue
        if child.name.startswith(".") or ".bak" in child.name:
            continue
        rel = child.relative_to(output_root)
        if child.is_dir():
            entries.append({
                "type": "directory", "name": child.name,
                "display_name": _display_name(child.name), "path": str(rel),
                "children": _build_tree(child, output_root, top_level=False),
            })
        elif child.suffix in _ALLOWED_EXTENSIONS:
            entries.append({
                "type": "file", "name": child.name,
                "display_name": _display_name(child.name), "path": str(rel),
                "extension": child.suffix, "size_bytes": child.stat().st_size,
            })
    return entries


_MAX_MARKDOWN_CHARS = 200_000  # some source docs are 2MB+ — cap so the browser doesn't choke


def _source_documents(pdf_dir: Path) -> List[Dict]:
    """
    Pre-converted markdown alternatives to Docling extraction — see
    ingest.py:_load_preconverted_md, which uses these instead of re-running
    OCR when present. Surfaced here read-only, under a "pdfs/" path prefix
    so /api/knowledge/file knows to resolve against pdf_dir instead of
    output_dir.
    """
    entries = []
    if not pdf_dir.exists():
        return entries
    for md_file in sorted(pdf_dir.glob("*_Converted.md")):
        entries.append({
            "type": "file", "name": md_file.name,
            "display_name": md_file.stem.replace("_Converted", "").replace("_", " "),
            "path": f"pdfs/{md_file.name}",
            "extension": ".md", "size_bytes": md_file.stat().st_size,
        })
    return entries


def _resolve_corpus_dirs(corpus_id: str) -> "tuple[Path, Path]":
    """Returns (pdf_dir, output_dir) for corpus_id — "default"/omitted keeps
    today's exact single-corpus behavior."""
    if corpus_id == "default":
        return PDF_DIR, OUTPUT_DIR
    if corpus_id == "guidelines":
        return GUIDELINES_PDF_DIR, GUIDELINES_OUTPUT_DIR
    _validate_patient_corpus_id(corpus_id)
    patient_dir = _patient_dir(corpus_id)
    if not patient_dir.exists():
        raise HTTPException(404, f"Unknown corpus '{corpus_id}'.")
    return patient_dir / "pdfs", patient_dir / "output"


@app.get("/api/knowledge/files")
def knowledge_files(corpus_id: str = "default") -> Dict:
    pdf_dir, output_dir = _resolve_corpus_dirs(corpus_id)
    tree = _build_tree(output_dir, output_dir)
    source_docs = _source_documents(pdf_dir)
    if source_docs:
        tree.insert(0, {
            "type": "directory", "name": "source_documents",
            "display_name": "Source Documents", "path": "pdfs",
            "children": source_docs,
        })
    return {"tree": tree}


@app.get("/api/knowledge/file")
def knowledge_file(path: str, corpus_id: str = "default") -> Dict:
    pdf_dir, output_dir = _resolve_corpus_dirs(corpus_id)
    if path.startswith("pdfs/"):
        target = (pdf_dir / path[len("pdfs/"):]).resolve()
        root = pdf_dir.resolve()
    else:
        target = (output_dir / path).resolve()
        root = output_dir.resolve()

    if not target.is_relative_to(root):
        raise HTTPException(400, "Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")

    if target.suffix == ".json":
        return {"type": "json", "content": json.loads(target.read_text(encoding="utf-8"))}

    text = target.read_text(encoding="utf-8")
    if len(text) > _MAX_MARKDOWN_CHARS:
        text = text[:_MAX_MARKDOWN_CHARS] + "\n\n---\n*Truncated — file is larger than the preview limit.*"
    return {"type": "markdown", "content": text}


# ── /api/visualize ───────────────────────────────────────────────────────────
# Optional, post-answer-only feature: turns an already-grounded answer +
# its citations into a Mermaid.js diagram spec. Deliberately generated from
# the FINAL ANSWER + EVIDENCE, never the raw user query — this is what keeps
# it grounded instead of a second, independent (and potentially hallucinated)
# generation. None of the text-gen providers in the fallback chain have an
# image-generation API, so this produces diagram-as-code (rendered
# client-side), not a raster image — Infographic and Comic Strip are
# intentionally NOT implemented here (see frontend).

_VIZ_TYPES = {
    "flowchart":    "a process flowchart — use Mermaid `flowchart TD` syntax showing sequential steps",
    "timeline":     "a timeline — use Mermaid `timeline` syntax showing chronological/version progression",
    "relationship": "a relationship diagram — use Mermaid `graph LR` syntax showing interconnected concepts, NOT a linear sequence",
    "mindmap":      "a mind map — use Mermaid `mindmap` syntax showing a central topic with branching sub-topics",
    "architecture": "an architecture diagram — use Mermaid `flowchart TD` syntax showing system components and how they interact",
}

_VIZ_SUGGEST_RULES = [
    (re.compile(r"chang(e|ed|es)?\b.*\bversion|\bversion.*\bchang|\bevolution\b|\bbetween v?\d", re.I), "timeline"),
    (re.compile(r"\brelat(e|ed|ionship)|\bconnect|\bassociat|\bversus\b|\bvs\b", re.I), "relationship"),
    (re.compile(r"\bhierarch|\bdomains?\b|\ball (the )?(topics|categories)|\blist all\b", re.I), "mindmap"),
    (re.compile(r"\barchitecture|\bsystem design\b|\bcomponents?\b interact|\bkubernetes\b", re.I), "architecture"),
    (re.compile(r"how does .* work|\bprocess\b|\bworkflow\b|\bexplain\b|\badjudicat", re.I), "flowchart"),
]


def _suggest_viz_type(question: str) -> str:
    for pattern, viz in _VIZ_SUGGEST_RULES:
        if pattern.search(question):
            return viz
    return "flowchart"


@app.get("/api/visualize/suggest")
def visualize_suggest(question: str) -> Dict:
    return {"suggested": _suggest_viz_type(question)}


class VisualizeRequest(BaseModel):
    question: str
    answer: str
    chunks: List[Dict] = []
    viz_type: str  # one of _VIZ_TYPES


_VISUALIZE_PROMPT = """You will produce a Mermaid.js diagram based STRICTLY on the grounded answer and evidence below. Do not introduce any fact, step, or relationship that is not explicitly present in the answer or evidence — if the answer doesn't specify enough detail for a rich diagram, keep the diagram simple rather than inventing detail.

Question: {question}

Answer: {answer}

Evidence:
{evidence}

Create {viz_hint}.

Requirements:
- Output ONLY a single Mermaid code block (```mermaid ... ```) — no other text before or after.
- Keep every label concise (a few words).
- Use only what's stated in the answer/evidence above.
- Clean and presentation-ready — enterprise style, no decorative elements.
"""


@app.post("/api/visualize")
def visualize(req: VisualizeRequest) -> Dict:
    if req.viz_type not in _VIZ_TYPES:
        raise HTTPException(400, f"Unsupported viz_type: {req.viz_type!r}")

    evidence = "\n".join(
        f"[{i + 1}] {c.get('section_header', '')}: {c.get('text', '')[:400]}"
        for i, c in enumerate(req.chunks[:6])
    ) or "(no additional evidence provided beyond the answer)"

    prompt = _VISUALIZE_PROMPT.format(
        question=req.question, answer=req.answer, evidence=evidence, viz_hint=_VIZ_TYPES[req.viz_type]
    )

    try:
        raw = llm_client.chat(prompt, max_tokens=1500)
    except Exception as e:
        raise HTTPException(500, str(e))

    match = re.search(r"```(?:mermaid)?\s*\n(.*?)```", raw, re.DOTALL)
    mermaid_code = match.group(1).strip() if match else raw.strip()
    return {"mermaid": mermaid_code, "viz_type": req.viz_type}
