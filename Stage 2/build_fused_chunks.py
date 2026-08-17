"""
Stage 2/build_fused_chunks.py
─────────────────────────────
Pipeline step that enriches each patient chunk with matched guideline context
from Stage 1's guideline KB, so that topic_summarizer.py's extraction prompt
sees BOTH the patient's raw text AND the relevant guideline summaries when
generating the patient's topic summaries.

Architecture:
  1. Load patient's chunks.json (raw OCR) and topic_registry.csv
  2. For each topic, use grounded_summary as a query to find matching
     guideline topics via cross_corpus_cli.py (subprocess — same pattern
     as fusion_worker.py, for the same config-collision reasons)
  3. Build a guideline context block per topic from matched summaries
  4. For each chunk belonging to that topic, append the guideline context
     to the chunk text
  5. Save as fused_chunk_text.json in the patient's output dir
  6. Set STAGE2_FUSED_CHUNKS_PATH env var so topic_summarizer.py picks it up

Runs BEFORE step_topic_summarizer() in Stage 2/run_tail.py.

Usage:
    cd "Stage 2"
    STAGE2_PATIENT_ID=dr-lalpath python build_fused_chunks.py
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

STAGE2_DIR = Path(__file__).parent
sys.path.insert(0, str(STAGE2_DIR))
import stage2_config as s2cfg  # noqa: E402


def _parse_chunk_ids(raw) -> List[str]:
    """Parse topic_registry.csv's chunk_ids column into a flat list of IDs.
    Handles formats: '[14_15]', '[9_10] | [15_16]', '14_15'."""
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if not raw or not isinstance(raw, str):
        return []
    brackets = [b.strip() for b in raw.split("|") if b.strip()]
    ids = []
    for b in brackets:
        b = b.strip().lstrip("[").rstrip("]")
        if b:
            ids.append(b)
    return ids


def _run_cross_corpus_batch(queries: List[Dict], k: int) -> Dict[str, List[Dict]]:
    """Shell out to cross_corpus_cli.py for batched guideline lookup."""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "queries.json"
        out_path = Path(tmp) / "results.json"
        in_path.write_text(json.dumps(queries), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(s2cfg.CROSS_CORPUS_CLI_PATH),
             "--in", str(in_path), "--out", str(out_path), "--k", str(k)],
            cwd=str(s2cfg.CROSS_CORPUS_CLI_PATH.parent),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.error(f"cross_corpus_cli.py failed:\n{result.stderr[-2000:]}")
            return {}

        payload = json.loads(out_path.read_text(encoding="utf-8"))
        return payload.get("results", {})


def _build_guideline_context_block(matches: List[Dict]) -> str:
    """Build a concise guideline context block from matched guideline topics."""
    if not matches:
        return ""
    parts = []
    for m in matches:
        label = m.get("master_label", "")
        summary = m.get("summarized_description") or m.get("grounded_summary", "")
        if label and summary:
            parts.append(f"[Guideline: {label}]\n{summary}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def build_fused_chunks(patient_id: str, guideline_match_k: int = 3) -> None:
    """
    Build fused_chunk_text.json for this patient by enriching each chunk
    with matched guideline context.

    Args:
        patient_id: The patient corpus ID (e.g. "dr-lalpath")
        guideline_match_k: Number of guideline topics to match per patient topic
    """
    s2cfg.setup_patient_env(patient_id)
    s2cfg.put_stage1_first_on_path()

    import config  # Stage 1's config.py, now env-pointed at the patient corpus

    chunks_path = config.CHUNKS_CACHE  # OUTPUT_DIR / chunks.json
    registry_path = config.REGISTRY_PATH  # OUTPUT_DIR / topic_registry.csv
    output_path = config.OUTPUT_DIR / "fused_chunk_text.json"

    if not chunks_path.exists():
        log.warning(f"No chunks.json at {chunks_path} — skipping fused chunk build")
        return
    if not registry_path.exists():
        log.warning(f"No topic_registry.csv at {registry_path} — skipping fused chunk build")
        return

    # ── Load patient chunks ──
    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)
    chunk_dict = {str(c["chunk_id"]): c for c in chunks}

    # ── Load topic registry ──
    import pandas as pd
    df = pd.read_csv(registry_path)

    # ── Build queries from topic descriptions ──
    queries = []
    topic_chunk_map: Dict[str, List[str]] = {}  # topic_label -> [chunk_ids]
    for _, row in df.iterrows():
        label = row.get("master_label", "")
        if not label or not isinstance(label, str):
            continue

        # Use grounded_summary if available, fall back to description
        query = str(row.get("grounded_summary", "") or row.get("description", "") or "")
        if not query.strip():
            continue

        chunk_ids = _parse_chunk_ids(row.get("chunk_ids", ""))
        if not chunk_ids:
            continue

        queries.append({"id": label, "query": query})
        topic_chunk_map[label] = chunk_ids

    if not queries:
        log.info("No topics with chunk_ids found — nothing to fuse")
        return

    # ── Batch cross-corpus lookup ──
    log.info(f"Looking up guideline matches for {len(queries)} patient topics (k={guideline_match_k})...")
    matches_by_topic = _run_cross_corpus_batch(queries, guideline_match_k)

    n_matched = sum(1 for v in matches_by_topic.values() if v)
    log.info(f"Guideline matches found for {n_matched}/{len(queries)} topics")

    # ── Build guideline context per chunk ──
    # Each chunk gets the guideline context of ALL topics it belongs to
    chunk_guideline_context: Dict[str, str] = {}
    for topic_label, chunk_ids in topic_chunk_map.items():
        matches = matches_by_topic.get(topic_label, [])
        if not matches:
            continue
        context_block = _build_guideline_context_block(matches)
        if not context_block:
            continue
        for cid in chunk_ids:
            if cid in chunk_dict:
                existing = chunk_guideline_context.get(cid, "")
                if context_block not in existing:
                    chunk_guideline_context[cid] = (
                        f"{existing}\n\n{context_block}" if existing else context_block
                    )

    # ── Build fused chunks ──
    fused_chunks = []
    n_enriched = 0
    for chunk in chunks:
        cid = str(chunk["chunk_id"])
        guideline_ctx = chunk_guideline_context.get(cid, "")
        if guideline_ctx:
            fused_chunk = dict(chunk)
            fused_chunk["text"] = (
                f"{chunk['text']}\n\n"
                f"--- MATCHED CLINICAL GUIDELINES ---\n"
                f"{guideline_ctx}"
            )
            fused_chunks.append(fused_chunk)
            n_enriched += 1
        else:
            fused_chunks.append(chunk)

    # ── Save fused chunks ──
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(fused_chunks, f, indent=2, ensure_ascii=False)

    log.info(f"Fused chunks written → {output_path}")
    log.info(f"  {n_enriched}/{len(fused_chunks)} chunks enriched with guideline context")

    # ── Set env var so topic_summarizer.py picks it up ──
    os.environ["STAGE2_FUSED_CHUNKS_PATH"] = str(output_path)
    log.info(f"  STAGE2_FUSED_CHUNKS_PATH set to {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build fused patient+guideline chunks")
    parser.add_argument("--patient-corpus-id", default=os.environ.get("STAGE2_PATIENT_ID", "default"))
    parser.add_argument("--k", type=int, default=3, help="Guideline topics to match per patient topic")
    args = parser.parse_args()

    build_fused_chunks(args.patient_corpus_id, guideline_match_k=args.k)


if __name__ == "__main__":
    main()
