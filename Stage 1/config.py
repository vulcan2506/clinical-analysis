import os as _os
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

# Loads Stage 1/.env (MISTRAL_API_KEY, etc.) — does not override already-set env vars.
_load_dotenv(Path(__file__).parent / ".env")

# ── Paths ──────────────────────────────────────────────────────────────────────
# Overridable so an isolated test corpus (e.g. a second document domain) can be
# run through the pipeline without touching the live data/pdfs, data/output.
PDF_DIR          = Path(_os.environ["STAGE1_PDF_DIR"]) if _os.environ.get("STAGE1_PDF_DIR") else Path("data/pdfs")
OUTPUT_DIR       = Path(_os.environ["STAGE1_OUTPUT_DIR"]) if _os.environ.get("STAGE1_OUTPUT_DIR") else Path("data/output")
REGISTRY_PATH    = OUTPUT_DIR / "topic_registry.csv"
CHUNKS_CACHE     = OUTPUT_DIR / "chunks.json"

# ── Models ─────────────────────────────────────────────────────────────────────
# Multilingual handles Hindi/Sanskrit terms in Indian polisci PDFs
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# ── LLM: llama.cpp server (Qwen3.5-9B-MTP Q4_K_M, GPU) ──────────────────────
# Start server with: ./start_server.sh before running the pipeline
LLAMA_SERVER_URL    = "http://127.0.0.1:8080"
LLAMA_MODEL_NAME    = "qwen35-9b"       # matches --alias in start_server.sh
LLAMA_PARALLEL_SLOTS = 6               # 6 parallel slots (-c 4096 -np 6); ~6x batch throughput

# ── LLM: text-generation backend ─────────────────────────────────────────────
# "local" routes llm_client.generate()/generate_batch() back to the llama.cpp
# server above with zero call-site changes (same abstraction the KT doc
# describes for the gemma→Qwen swap — only this pointer changes).
LLM_BACKEND          = _os.environ.get("LLM_BACKEND", "api")  # "api" | "local"
API_PARALLEL_SLOTS   = 6                # concurrent request cap for generate_batch()

# 4-tier fallback chain for LLM_BACKEND="api" (auth failure, rate limit,
# outage, refusal at any tier falls through to the next):
#   1. Mistral — same account as the OCR chain below
#   2. OpenRouter — free-tier model, OpenAI-compatible endpoint
#   3. Groq — OpenAI-compatible endpoint
#   4. Local llama.cpp server (auto-started if not already running)
# Anthropic was removed from this chain entirely (2026-07-14) — the key on
# file was returning 401 on every call, so every single text-generation call
# in the pipeline wasted 3 failed attempts before cascading to OpenRouter/Groq
# anyway. No longer used anywhere in Stage 1.
#
# openai/gpt-oss-120b:free was verified DEAD this session (404 — moved to a
# paid-only slug); google/gemma-4-26b-a4b-it:free was verified live with a
# short token budget. Some OpenRouter free models (e.g. openai/gpt-oss-20b)
# are reasoning models that can burn an entire short max_tokens budget on
# hidden chain-of-thought and return empty content — verify short-budget
# behavior specifically before swapping this default again, not just that
# the model responds at all.
OPENROUTER_API_KEY   = _os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL     = _os.environ.get("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free")
OPENROUTER_BASE_URL  = "https://openrouter.ai/api/v1"

GROQ_API_KEY         = _os.environ.get("GROQ_API_KEY")
GROQ_MODEL           = _os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL    = _os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GROQ_BASE_URL        = "https://api.groq.com/openai/v1"
# Groq's on-demand tier caps llama-4-scout at 30,000 tokens/minute; a single
# rendered page (image + prompt) runs ~3,000 tokens — close to the sustainable
# per-page rate on its own. Concurrency=2 caused two threads to retry in
# lockstep and repeatedly collide over the same refill window (each 429,
# back off, retry — together — and collide again); serializing at 1 avoids
# that entirely. The retry/backoff pair below rides out the occasional 429
# that still happens near the window boundary.
GROQ_VISION_PARALLEL_SLOTS      = 1
GROQ_VISION_RATE_LIMIT_RETRIES  = 4     # attempts per page before giving up (→ next OCR tier)
GROQ_VISION_RATE_LIMIT_BACKOFF  = 20    # seconds to wait between retries — long enough for
                                         # a meaningful chunk of the 60s TPM window to roll off

MISTRAL_API_KEY      = _os.environ.get("MISTRAL_API_KEY")
MISTRAL_OCR_MODEL    = _os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")
MISTRAL_CHAT_MODEL   = _os.environ.get("MISTRAL_CHAT_MODEL", "mistral-small-latest")  # primary text-gen tier
# Mistral's dedicated document-OCR endpoint processes an entire PDF in one
# request (not per-page like the Groq fallback), so there's no per-page
# concurrency or TPM-budget knob needed here.

LOCAL_FALLBACK_STARTUP_TIMEOUT = 90     # seconds to wait for start_server.sh to report healthy
LOCAL_FALLBACK_POLL_INTERVAL   = 2.0    # seconds between health-check polls
LOCAL_FALLBACK_HEALTH_TIMEOUT  = 2.0    # per-request timeout for the health check itself

# ── OCR: 3-tier chain — Mistral (primary) → Groq (fallback 1) → Docling (fallback 2)
# Each tier is only tried if the previous one raises an exception (API error,
# timeout, refusal), not on output-quality heuristics. Docling is the only
# tier with no external API dependency.
USE_VISION_OCR       = _os.environ.get("USE_VISION_OCR", "true").lower() == "true"
OCR_PAGE_DPI         = 150               # page render resolution fed to Groq vision fallback
OCR_MAX_TOKENS       = 4096              # per-page transcription budget (Groq fallback tier)

# ── Context-shift chunking ─────────────────────────────────────────────────────
# Sentences are split into a new chunk when cosine similarity between
# consecutive sentence embeddings drops below this threshold.
# Lower = more sensitive to topic shifts (more, smaller chunks)
# Higher = less sensitive (fewer, larger chunks)
CONTEXT_SHIFT_THRESHOLD = 0.45

CHUNK_MIN_TOKENS = 60    # merge chunks shorter than this into the previous
CHUNK_MAX_TOKENS = 450   # hard ceiling — force split even if similarity is high

# ── Noise filtering ────────────────────────────────────────────────────────────
# Alpha ratio: paragraphs where < this fraction of chars are alphabetic
# are flagged as noise (tables, citation lists, number-heavy content).
# Was 0.55 — relaxed to 0.40 to stop dropping legitimate scholarly text
# with dates, percentages, and citations.
ALPHA_RATIO_THRESHOLD = 0.40

# TOC/index page detection: if this fraction of lines on a page are short
# (< TOC_LINE_WORD_LIMIT words), the whole page is skipped.
TOC_SHORT_LINE_RATIO  = 0.65
TOC_LINE_WORD_LIMIT   = 8

# LLM noise filter — runs after chunking (set False to skip, faster)
USE_LLM_NOISE_FILTER = False  # skips a full LLM pass — heuristic filter is enough

# ── KeyBERT ────────────────────────────────────────────────────────────────────
KEYBERT_TOP_N     = 5
KEYBERT_NGRAM_MIN = 1
KEYBERT_NGRAM_MAX = 2
KEYBERT_DIVERSITY = 0.5   # MMR: 0=redundant, 1=maximally diverse

# ── Master labeling ────────────────────────────────────────────────────────────
LABEL_MAX_TOKENS        = 200   # Qwen3-14B is concise — 350 was wasteful
DESCRIPTION_MAX_TOKENS  = 100   # reduced from 150
DESCRIPTION_MIN_LENGTH  = 20    # descriptions shorter than this are treated as missing

# ── Description merging ────────────────────────────────────────────────────────
# Max individual chunk descriptions fed to the merge prompt.
# Groups with more chunks than this get the top-N by description length.
MERGE_MAX_DESCRIPTIONS  = 8
MERGE_MAX_TOKENS        = 150   # reduced from 250

# ── Description enrichment ─────────────────────────────────────────────────────
# Chunks with quality score below this get sent through enrichment routes
ENRICHMENT_QUALITY_THRESHOLD = 0.40

# Route 1 — Internal RAG
# How many similar chunks from same source_doc to pull as context
ENRICHMENT_INTERNAL_TOP_K    = 4

# Route 2 — Web search fallback
# Set False to disable web search entirely (offline environments)
USE_WEB_ENRICHMENT           = True
ENRICHMENT_WEB_MAX_RESULTS   = 3
ENRICHMENT_MAX_TOKENS        = 150   # reduced from 250

# ── Batching ───────────────────────────────────────────────────────────────────
# KeyBERT: how many chunk texts per extract_keywords() call
# Was: 1 per chunk → N×"Batches:1/1" bars
# Now: ceil(N/32) bars total
KEYBERT_BATCH_SIZE  = 32

# LLM rectification: how many chunks sent to pipe() per call
RECTIFY_BATCH_SIZE  = 16

# ── Grouper guards ────────────────────────────────────────────────────────────
MAX_RULE_GROUP_SIZE = 8       # Stage 1: refuse merge if combined group exceeds this
STAGE2_OVERLAP      = 2       # Stage 2: overlapping chunks between consecutive LLM windows
GROUP_COHESION_THRESHOLD = 0.40  # Stage 2.5: split merged groups where cosine sim drops below this
# ── Multiprocessing ────────────────────────────────────────────────────────────
# Pages per worker batch for large PDFs (> PAGE_BATCH_SIZE pages)
# 800-page PDF at 30 pages/batch = 27 batches across N_WORKERS cores
PAGE_BATCH_SIZE = 30
# Workers for parallel page extraction (CPU-bound, no GPU needed)
# Set to os.cpu_count() // 2 for safe default, or override manually
N_WORKERS = max(2, (_os.cpu_count() or 4) // 2)

# ── Nested taxonomy ────────────────────────────────────────────────────────────
MACRO_THRESHOLD         = 0.50
MICRO_THRESHOLD         = 0.60
NESTED_OUTPUT_PATH      = OUTPUT_DIR / "enterprise_nested_topics.json"

# ── Context profiler ──────────────────────────────────────────────────────────
PROFILE_CACHE_DIR       = OUTPUT_DIR / "profiles"
PROMPT_OUTPUT_DIR       = OUTPUT_DIR / "prompts"

# ── Registry summarization ─────────────────────────────────────────────────────
SUMMARY_MAX_TOKENS      = 280  # 80 output + 200 thinking overhead

# ── Label normalization ───────────────────────────────────────────────────────
LABEL_MERGE_THRESHOLD   = 0.82  # Cosine similarity above which labels are merged
LABEL_MIN_WORDS         = 2     # Labels shorter than this are flagged as garbage

# ── Cross-version matching ────────────────────────────────────────────────────
CROSS_VERSION_MATCH_THRESHOLD = 0.75  # Embedding similarity for cross-doc label merge