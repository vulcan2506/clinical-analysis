"""
corpus_registry.py
────────────────────
Small in-process registry mapping corpus_id -> (chroma_dir, index_dir),
consulted by router.get_router()/chroma_store.get_store() for any corpus_id
other than "default". Lets Stage 1's permanent guideline KB and any number
of Stage 2 patient runs coexist as separate live corpora within one
retrieval_layer process, without forking router.py/chroma_store.py/
retriever.py/redis_cache.py — those stay unmodified aside from threading a
corpus_id parameter through.

"default" itself is never looked up here — it resolves directly to
config.CHROMA_DIR/config.INDEX_DIR inside get_store()/get_router(), so
existing zero-arg callers are completely unaffected by this module's
contents.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import config

_RETRIEVAL_DIR = Path(__file__).parent


@dataclass(frozen=True)
class CorpusPaths:
    chroma_dir: Path
    index_dir: Path


_lock = threading.Lock()

# Pre-seeded with the permanent guideline KB. Stage 1/data/ already holds the
# 13 clinical guideline PDFs processed (and indexed into config.CHROMA_DIR/
# config.INDEX_DIR) as the "default" corpus, before the guidelines/patient
# corpus_id split existed — "guidelines" is therefore an ALIAS onto that same
# already-built storage, not a separate chroma_db_guidelines/index_guidelines
# build target. Patient (Stage 2) corpus_ids register themselves at
# run-completion time via register() below.
_CORPUS_PATHS: Dict[str, CorpusPaths] = {
    "guidelines": CorpusPaths(
        chroma_dir=config.CHROMA_DIR,
        index_dir=config.INDEX_DIR,
    ),
}


def resolve(corpus_id: str) -> CorpusPaths:
    if corpus_id not in _CORPUS_PATHS:
        raise KeyError(
            f"Unknown corpus_id '{corpus_id}' — register it first via "
            f"corpus_registry.register(), or use 'default'/'guidelines'."
        )
    return _CORPUS_PATHS[corpus_id]


def register(corpus_id: str, chroma_dir: Path, index_dir: Path) -> None:
    with _lock:
        _CORPUS_PATHS[corpus_id] = CorpusPaths(chroma_dir=Path(chroma_dir), index_dir=Path(index_dir))


def is_registered(corpus_id: str) -> bool:
    return corpus_id == "default" or corpus_id in _CORPUS_PATHS


def list_corpus_ids() -> list:
    return ["default"] + list(_CORPUS_PATHS.keys())
