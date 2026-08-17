"""
llm_client.py
─────────────
Text-generation client with a 4-tier fallback chain: Mistral → OpenRouter →
Groq → local llama.cpp server. LLM_BACKEND="api" (default) uses the full
chain; LLM_BACKEND="local" skips straight to the local server.

The local llama.cpp server runs Qwen3-14B-Q4_K_M — no external API, no rate
limits. Start it before running the pipeline if using LLM_BACKEND="local":
  bash start_server.sh

── Dynamic token budgeting ──────────────────────────────────────────────────
Each call site should pass stop sequences matching its expected output format.
This terminates generation the moment the output is complete, freeing the
parallel slot for the next prompt without waiting for unused token budget.

Use the budget() helper to right-size max_tokens from input length:
  max_tokens=llm_client.budget(prompt, ratio=4, ceil=200)

Stop sequences by output type (pass as stop=[...]):
  JSON object   → stop=STOP_JSON
  YES/NO flag   → stop=STOP_FLAG
  Free text     → stop=STOP_TEXT
  Short label   → stop=STOP_LABEL
"""

import gc
import time
import logging
import subprocess
import concurrent.futures
from pathlib import Path
from typing import List, Optional

import httpx
from openai import OpenAI

import config

log = logging.getLogger(__name__)

_mistral_client = None    # lazy — only imported/constructed for ingest.py's primary OCR path
_local_server_starting = False  # guards against launching start_server.sh twice from parallel threads


def _is_local_server_up() -> bool:
    try:
        r = httpx.get(f"{config.LLAMA_SERVER_URL}/health", timeout=config.LOCAL_FALLBACK_HEALTH_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _ensure_local_server() -> bool:
    """
    Starts the local llama.cpp server (start_server.sh) if it isn't already
    reachable, then polls /health until it responds or LOCAL_FALLBACK_STARTUP_TIMEOUT
    is hit. Returns True once the server is confirmed up, False otherwise.
    Safe to call from multiple threads — only the first caller launches the
    process; the rest just poll.
    """
    global _local_server_starting

    if _is_local_server_up():
        return True

    script = Path(__file__).parent / "start_server.sh"
    if not _local_server_starting:
        if not script.exists():
            log.error(f"Cannot start local fallback server — {script} not found")
            return False
        _local_server_starting = True
        log.warning("API tiers unavailable — starting local llama.cpp server (start_server.sh) as fallback...")
        log_path = config.OUTPUT_DIR / "llama_server_fallback.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as logf:
            subprocess.Popen(
                ["bash", str(script)],
                cwd=str(script.parent),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # detach — survives after this process/thread
            )

    waited = 0.0
    while waited < config.LOCAL_FALLBACK_STARTUP_TIMEOUT:
        time.sleep(config.LOCAL_FALLBACK_POLL_INTERVAL)
        waited += config.LOCAL_FALLBACK_POLL_INTERVAL
        if _is_local_server_up():
            log.info(f"Local llama.cpp server is up after {waited:.0f}s")
            return True

    log.error(f"Local llama.cpp server did not become ready within {config.LOCAL_FALLBACK_STARTUP_TIMEOUT}s")
    return False


def _get_mistral_client():
    global _mistral_client
    if _mistral_client is None:
        if not config.MISTRAL_API_KEY:
            raise RuntimeError(
                "MISTRAL_API_KEY is not set — add it to Stage 1/.env — MISTRAL_API_KEY=... "
                "(never paste it into chat/logs)."
            )
        try:
            from mistralai.client import Mistral
        except ModuleNotFoundError as e:
            # A missing mistralai package is a broken environment, not a
            # transient API failure — swallowing it (as the generic except in
            # _chat does) silently downgrades every call to the free OpenRouter
            # tier, which hard-caps output and produces degraded/truncated
            # summaries. Fail loudly so the venv/wrong-interpreter bug surfaces.
            raise ModuleNotFoundError(
                "mistralai is not installed — running outside the project venv? "
                f"Use ./venv/bin/python (original error: {e}). "
                "Fallback tiers cannot replace the primary extraction model."
            ) from e
        _mistral_client = Mistral(api_key=config.MISTRAL_API_KEY)
        # Shared client — used for both OCR (ingest.py, model=MISTRAL_OCR_MODEL)
        # and chat (_chat_mistral, model=MISTRAL_CHAT_MODEL), so no single
        # model name belongs in this construction-time log line.
        log.info(f"Mistral client constructed — OCR model={config.MISTRAL_OCR_MODEL}, chat model={config.MISTRAL_CHAT_MODEL}")
    return _mistral_client


def _progress(done: int, total: int, desc: str, t0: float) -> None:
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0
    rem = (total - done) / rate if rate > 0 else 0
    log.info(f"{desc}: {done}/{total} ({100*done//total}%) — ~{int(rem//60)}m{int(rem%60):02d}s remaining")

# ── Stop sequence constants ───────────────────────────────────────────────────
STOP_JSON  = ["```\n", "\n\n"]        # JSON block ends at closing brace line
STOP_FLAG  = ["\n", ".", ",", " "]   # YES/NO/score — stop after first token
STOP_TEXT  = ["\n\n\n"]              # Free-text paragraphs — stop at blank line
STOP_LABEL = ["\n\n", "```"]         # Short structured outputs

_client: Optional[OpenAI] = None
_openrouter_client: Optional[OpenAI] = None
_groq_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=config.LLAMA_SERVER_URL + "/v1",
            api_key="none",
        )
        log.info(f"llama.cpp client → {config.LLAMA_SERVER_URL}")
    return _client


def _get_openrouter_client() -> Optional[OpenAI]:
    global _openrouter_client
    if not config.OPENROUTER_API_KEY:
        return None
    if _openrouter_client is None:
        _openrouter_client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
        log.info(f"OpenRouter client → model={config.OPENROUTER_MODEL}")
    return _openrouter_client


def _get_groq_client() -> Optional[OpenAI]:
    global _groq_client
    if not config.GROQ_API_KEY:
        return None
    if _groq_client is None:
        _groq_client = OpenAI(
            base_url=config.GROQ_BASE_URL,
            api_key=config.GROQ_API_KEY,
        )
        log.info(f"Groq client → model={config.GROQ_MODEL}")
    return _groq_client


def budget(
    prompt: str,
    ratio: float = 4.0,
    floor: int = 50,
    ceil: int = 400,
) -> int:
    """
    Estimate output token budget from input prompt length.

    ratio: input_words / ratio = expected output tokens
           Lower ratio = more output relative to input (complex tasks)
           Higher ratio = less output relative to input (extraction/classification)
    floor: minimum tokens regardless of input size
    ceil:  hard upper limit — prevents runaway on edge cases
    """
    input_words = len(prompt.split())
    return max(floor, min(ceil, int(input_words / ratio)))


def _chat(
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_prompt: Optional[str],
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> str:
    if config.LLM_BACKEND == "api":
        try:
            return _chat_mistral(prompt, max_tokens, temperature, system_prompt, stop, enable_thinking)
        except ModuleNotFoundError:
            # Missing mistralai = broken environment (wrong interpreter / venv
            # not activated), NOT a transient API failure. Do NOT fall through
            # to the free OpenRouter tier — it hard-caps output well below
            # max_tokens and silently degrades extraction. Fail loudly instead.
            raise
        except Exception as e_mistral:
            log.warning(f"Mistral call failed after retries ({type(e_mistral).__name__}: {e_mistral}) — trying OpenRouter fallback")

        or_client = _get_openrouter_client()
        if or_client is not None:
            try:
                return _chat_openai_compatible(
                    or_client, config.OPENROUTER_MODEL, prompt, max_tokens, temperature,
                    system_prompt, stop, enable_thinking, label="OpenRouter",
                )
            except Exception as e_or:
                log.warning(f"OpenRouter call failed after retries ({type(e_or).__name__}: {e_or}) — trying Groq fallback")
        else:
            log.warning("OPENROUTER_API_KEY not set — skipping OpenRouter, trying Groq")

        groq_client = _get_groq_client()
        if groq_client is not None:
            try:
                return _chat_openai_compatible(
                    groq_client, config.GROQ_MODEL, prompt, max_tokens, temperature,
                    system_prompt, stop, enable_thinking, label="Groq",
                )
            except Exception as e_groq:
                log.warning(f"Groq call failed after retries ({type(e_groq).__name__}: {e_groq}) — falling back to local llama.cpp server")
        else:
            log.warning("GROQ_API_KEY not set — skipping Groq, trying local llama.cpp server")

        if not _ensure_local_server():
            raise RuntimeError(
                "Mistral, OpenRouter, and Groq all failed, and the local llama.cpp "
                "fallback server did not come up"
            )
        return _chat_local(prompt, max_tokens, temperature, system_prompt, stop, enable_thinking)
    return _chat_local(prompt, max_tokens, temperature, system_prompt, stop, enable_thinking)


def _chat_mistral(
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_prompt: Optional[str],
    stop: Optional[List[str]],
    enable_thinking: bool,
) -> str:
    """enable_thinking is accepted for signature parity with the other _chat_*
    helpers but unused — Mistral's chat.complete() has no equivalent."""
    client = _get_mistral_client()
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    kwargs = dict(
        model=config.MISTRAL_CHAT_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if stop:
        kwargs["stop"] = stop

    for attempt in range(3):
        try:
            resp = client.chat.complete(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            if attempt < 2:
                log.warning(f"Mistral API error (attempt {attempt+1}/3), retrying...")
            else:
                log.error(f"Mistral API error after 3 attempts: {e}")
                raise


def _chat_openai_compatible(
    client: OpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_prompt: Optional[str],
    stop: Optional[List[str]],
    enable_thinking: bool,
    label: str,
) -> str:
    """Shared dispatch for any OpenAI-compatible fallback endpoint (OpenRouter, Groq).
    enable_thinking is accepted for signature parity with _chat_local but unused —
    neither provider's chat.completions endpoint supports llama.cpp's
    chat_template_kwargs extension."""
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    kwargs = dict(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if stop:
        kwargs["stop"] = stop

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < 2:
                log.warning(f"{label} API error (attempt {attempt+1}/3), retrying...")
            else:
                log.error(f"{label} API error after 3 attempts: {e}")
                raise


def _chat_local(
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_prompt: Optional[str],
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> str:
    client = _get_client()
    messages = [
        {"role": "user", "content": prompt},
    ]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    kwargs = dict(
        model=config.LLAMA_MODEL_NAME,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    if stop:
        kwargs["stop"] = stop

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < 2:
                log.warning(f"llama.cpp timeout (attempt {attempt+1}/3), retrying...")
            else:
                log.error(f"llama.cpp inference error after 3 attempts: {e}")
                raise


# ── Public API ────────────────────────────────────────────────────────────────

def generate(
    prompt: str,
    max_tokens: int = 500,
    temperature: float = 0.0,
    system_prompt: Optional[str] = None,
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> str:
    return _chat(prompt, max_tokens, temperature, system_prompt, stop, enable_thinking)


def generate_batch(
    prompts: List[str],
    max_tokens: int = 500,
    temperature: float = 0.0,
    system_prompt: Optional[str] = None,
    desc: str = "LLM Inference",
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> List[str]:
    if not prompts:
        return []

    results: List[Optional[str]] = [None] * len(prompts)
    total = len(prompts)
    milestone = max(1, total // 10)  # log every 10%

    def _call(idx_prompt):
        idx, prompt = idx_prompt
        return idx, _chat(prompt, max_tokens, temperature, system_prompt, stop, enable_thinking)

    slots = config.API_PARALLEL_SLOTS if config.LLM_BACKEND == "api" else config.LLAMA_PARALLEL_SLOTS
    t0 = time.time()
    log.info(f"{desc}: starting {total} items ({slots} parallel slots, backend={config.LLM_BACKEND})")
    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as pool:
        futures = {pool.submit(_call, (i, p)): i for i, p in enumerate(prompts)}
        for fut in concurrent.futures.as_completed(futures):
            try:
                idx, text = fut.result()
            except Exception as e:
                idx = futures[fut]
                log.warning(f"Slot {idx} failed ({type(e).__name__}) — returning empty")
                text = ""
            results[idx] = text
            done += 1
            if done % milestone == 0 or done == total:
                _progress(done, total, desc, t0)

    return results


def generate_local(
    prompt: str,
    max_tokens: int = 50,
    system_prompt: Optional[str] = None,
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> str:
    return _chat(prompt, max_tokens, 0.0, system_prompt, stop, enable_thinking)


def generate_local_batch(
    prompts: List[str],
    max_tokens: int = 50,
    batch_size: int = 16,  # noqa: ARG001 — kept for call-site compatibility
    system_prompt: Optional[str] = None,
    desc: str = "Local LLM Inference",
    stop: Optional[List[str]] = None,
    enable_thinking: bool = False,
) -> List[str]:
    return generate_batch(
        prompts,
        max_tokens=max_tokens,
        temperature=0.0,
        system_prompt=system_prompt,
        desc=desc,
        stop=stop,
        enable_thinking=enable_thinking,
    )


def get_mistral_client():
    """Public accessor — used by ingest.py's Mistral OCR path (primary), which
    needs raw client.ocr.process() for the dedicated document-OCR endpoint
    that the text-only generate()/generate_batch() helpers don't expose."""
    return _get_mistral_client()


def get_groq_client() -> OpenAI:
    """Public accessor — used by ingest.py's Groq vision OCR path, which needs
    raw client.chat.completions.create() for multimodal (image_url) content
    blocks that the text-only generate()/generate_batch() helpers don't expose.
    Unlike the private _get_groq_client() used in the text-generation fallback
    chain (which returns None so callers can skip to the next tier), this
    raises when the key is missing — ingest.py's OCR call site wants a hard
    failure so it falls back to Docling."""
    client = _get_groq_client()
    if client is None:
        raise RuntimeError(
            "GROQ_API_KEY is not set — add it to Stage 1/.env — GROQ_API_KEY=gsk_... "
            "(never paste it into chat/logs)."
        )
    return client


def unload():
    global _client
    _client = None
    gc.collect()
    log.info("llm_client reset (llama-server keeps model loaded)")
