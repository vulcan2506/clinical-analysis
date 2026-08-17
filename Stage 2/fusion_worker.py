"""
Stage 2/fusion_worker.py
──────────────────────────
Pure, stateless compute for ONE patient topic's guideline conformance —
delta comparison and (if the delta is constructive) an evolution card.
Invoked as a subprocess per request by retrieval_layer/api_server.py's
guideline-conformance endpoint, which owns all caching (Redis) — this file
never reads or writes delta_jobs_cache.json/evolution_cards_cache.json,
never touches Redis, never persists anything. Print one JSON blob to
stdout's --out file and exit.

Runs in Stage-1/Stage-2 module context (needs delta_analyzer.py's
_fusion_mode machinery and evolution_analyzer.py's run_evolution_jobs()),
so it cannot import retrieval_layer's cross_corpus.py in-process — see
retrieval_layer/cross_corpus_cli.py's docstring for the exact reason
(config module-name collision + env-override poisoning). Shells out to that
CLI (a further nested subprocess) for the live guideline-topic lookup.

Usage:
    python fusion_worker.py --patient-corpus-id dr-lal-path \\
        --topic "Glycemic Control Guidelines" --out result.json [--k 3]
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

STAGE2_DIR = Path(__file__).parent
sys.path.insert(0, str(STAGE2_DIR))

import stage2_config as s2cfg  # noqa: E402


def _compound_chunk_id(chunk_ids_raw: str) -> str:
    """
    Mirrors the deleted guideline_fusion.py's _compound_chunk_id() —
    strips topic_registry.csv's chunk_ids column bracket formatting
    ("[25_26_27_28_29_31]" or "[9_10] | [15_16]" for multi-source-doc
    topics) into the single underscore-joined compound id
    delta_analyzer._get_chunk_text() already knows how to resolve.
    """
    if not chunk_ids_raw or not isinstance(chunk_ids_raw, str):
        return ""
    brackets = [b.strip() for b in chunk_ids_raw.split("|") if b.strip()]
    return "_".join(b.strip().lstrip("[").rstrip("]") for b in brackets if b)


def _run_cross_corpus_lookup(query: str, topic_label: str, k: int) -> "tuple[List[Dict], str]":
    """Shells out to retrieval_layer/cross_corpus_cli.py — see this file's
    module docstring for why in-process import is unsafe here."""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "queries.json"
        out_path = Path(tmp) / "results.json"
        in_path.write_text(json.dumps([{"id": topic_label, "query": query}]), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(s2cfg.CROSS_CORPUS_CLI_PATH),
             "--in", str(in_path), "--out", str(out_path), "--k", str(k)],
            cwd=str(s2cfg.CROSS_CORPUS_CLI_PATH.parent),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cross_corpus_cli.py failed: {result.stderr[-2000:]}")

        payload = json.loads(out_path.read_text(encoding="utf-8"))
        return payload["results"].get(topic_label, []), payload["guideline_kb_version"]


def run(patient_corpus_id: str, topic_label: str, k: int) -> Dict:
    s2cfg.setup_patient_env(patient_corpus_id)
    s2cfg.put_stage1_first_on_path()

    import config  # Stage 1's config.py, now env-pointed at the patient corpus
    import pandas as pd
    import context_profiler
    from delta_analyzer import run_delta_jobs, _get_chunk_text
    from evolution_analyzer import run_evolution_jobs

    df = pd.read_csv(config.REGISTRY_PATH)
    row_matches = df[df["master_label"] == topic_label]
    if row_matches.empty:
        return {"topic": topic_label, "matches": 0, "delta": [], "evolution": []}
    row = row_matches.iloc[0]

    query = row.get("grounded_summary") or row.get("summarized_description") or ""
    if not query or not isinstance(query, str):
        return {"topic": topic_label, "matches": 0, "delta": [], "evolution": []}

    guideline_matches, guideline_kb_version = _run_cross_corpus_lookup(query, topic_label, k)
    if not guideline_matches:
        return {
            "topic": topic_label, "matches": 0,
            "guideline_kb_version": guideline_kb_version,
            "delta": [], "evolution": [],
        }

    with open(config.CHUNKS_CACHE, "r", encoding="utf-8") as f:
        patient_chunk_dict = {str(c["chunk_id"]): c["text"] for c in json.load(f)}

    patient_chunk_id = _compound_chunk_id(row.get("chunk_ids", ""))
    text_A = _get_chunk_text(patient_chunk_dict, patient_chunk_id)
    if not text_A:
        return {"topic": topic_label, "matches": 0, "delta": [], "evolution": []}

    forced_profile = context_profiler.get_profile("")  # forced profile short-circuits on source_doc regardless
    analyst_role = (forced_profile or {}).get("analyst_role", "a clinical analyst")
    doc_purpose  = (forced_profile or {}).get("document_purpose", "clinical documentation")

    jobs: List[Dict] = []
    for m in guideline_matches:
        text_B = m.get("chunk_text", "")
        if not text_B:
            continue
        jobs.append({
            "parent": "", "sub": "",
            "topic": topic_label,
            "vA": "Patient", "vB": "Guideline",
            "id_A": patient_chunk_id, "id_B": m["chunk_id"],
            "desc_A": str(row.get("description", "")), "desc_B": m.get("summarized_description", ""),
            "text_A": text_A, "text_B": text_B,
            "qna_A": [], "qna_B": [],
            "_analyst_role": analyst_role, "_doc_purpose": doc_purpose,
            "_profile": forced_profile,
            "_fusion_mode": True,
            "_guideline_score": m.get("score"),
        })

    if not jobs:
        return {
            "topic": topic_label, "matches": 0,
            "guideline_kb_version": guideline_kb_version,
            "delta": [], "evolution": [],
        }

    run_delta_jobs(jobs)  # mutates jobs in place: profile_A/profile_B/delta

    # Normalize delta to a plain dict BEFORE the constructive filter — the
    # filter reads change_type here, and run_evolution_jobs() (below) does
    # its own dict normalization internally, which would otherwise leave
    # constructive jobs' "delta" as a dict while non-constructive jobs' stays
    # a DeltaResult object (job dicts are mutated in place, and
    # constructive_jobs holds references into the same objects as jobs).
    for j in jobs:
        d = j.get("delta")
        j["delta"] = d.model_dump() if hasattr(d, "model_dump") else d

    # fusion_change_types is a small, fixed, known-in-advance vocabulary
    # (Concordant with Guideline / Deviates - Clinically Significant /
    # Deviates - Borderline / Guideline Silent on This Case) — unlike
    # Stage 1's own delta_change_types (domain-invented per corpus, needs
    # evolution_analyzer._is_constructive()'s exclude-list approach), so an
    # include-check on "silent" is enough here: "Guideline Silent on This
    # Case" means the matched guideline genuinely has nothing to say about
    # this patient finding — the delta's own analysis says so explicitly
    # ("no overlap...") — so manufacturing a foundation/value-added
    # narrative there would assert a connection the delta itself just
    # denied. The other three categories all represent a real, substantive
    # guideline<->patient relationship worth an evolution card, whether
    # that relationship is agreement or a flagged deviation.
    constructive_jobs = [
        j for j in jobs
        if j.get("delta") and "silent" not in (j["delta"].get("change_type") or "").lower()
    ]
    if constructive_jobs:
        run_evolution_jobs(constructive_jobs)  # mutates in place: job["card"] on success

    delta_out = []
    evolution_out = []
    for j in jobs:
        d = j.get("delta") or {}
        delta_out.append({
            "guideline_label": j["id_B"],
            "guideline_score": j.get("_guideline_score"),
            "change_type": d.get("change_type"),
            "analysis": d.get("analysis"),
            "key_differences": d.get("key_differences"),
            "confidence": d.get("confidence"),
        })
        card = j.get("card")
        if card:
            evo_entry = {
                "guideline_label": j["id_B"],
                "feature_name": card.feature_name,
                "foundation": card.foundation,
                "value_added": card.value_added,
                "narrative": card.narrative,
                "change_type": card.change_type,
            }
            if card.clinical_finding:
                evo_entry["clinical_finding"] = card.clinical_finding
            if card.guideline_context:
                evo_entry["guideline_context"] = card.guideline_context
            if card.clinical_significance:
                evo_entry["clinical_significance"] = card.clinical_significance
            evolution_out.append(evo_entry)

    return {
        "topic": topic_label,
        "matches": len(jobs),
        "guideline_kb_version": guideline_kb_version,
        "delta": delta_out,
        "evolution": evolution_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient-corpus-id", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--k", type=int, default=None)
    args = parser.parse_args()

    k = args.k if args.k is not None else s2cfg.GUIDELINE_MATCH_TOP_K
    result = run(args.patient_corpus_id, args.topic, k)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
