"""
retrieval_layer/smoke_test_fusion.py
──────────────────────────────────────
Repeatable smoke test for the live, confidence-based cross-corpus fusion
(replaced the offline splice mechanism 2026-07-17 — see the
project_clinical_guideline_fusion memory for the full history). Exercises
every new piece against a real, already-processed patient corpus — no
reprocessing needed, just an existing patient output dir.

Checks, in order:
  1. reverse_map_integrity   — every guideline chunk_id resolves to exactly
                                one topic (no orphans). Local, no LLM cost.
  2. cross_corpus_boundary   — cross_corpus_cli.py still resolves the
                                GUIDELINE corpus, not the patient corpus,
                                when invoked under a deliberately
                                Stage-2-poisoned environment (the exact
                                failure mode this design exists to guard
                                against). Also picks the best-matched
                                patient topic to drive checks 3-5, so this
                                test works against ANY processed patient
                                corpus, not one hardcoded topic name. Local,
                                no LLM cost beyond embedding+rerank.
  3. enhanced_summary        — GET .../enhanced-summary against the live
                                api_server.py. Needs the dev server up;
                                cleanly SKIPped (not failed) if it isn't.
  4. guideline_conformance   — GET .../guideline-conformance against the
                                live api_server.py. LLM-heavy (delta +
                                evolution) — pass --skip-conformance for a
                                fast run. Same skip-if-unreachable behavior.
  5. chat_two_hop            — retriever.retrieve() directly, in-process,
                                NOT via the live server — sidesteps
                                corpus_registry's in-memory registration
                                lifecycle (unrelated pre-existing plumbing
                                this test isn't meant to cover). Asserts
                                both the patient corpus AND "guidelines"
                                appear in the merged, deduped chunk list.

Usage:
    cd retrieval_layer
    python smoke_test_fusion.py [--patient-corpus-id dr-lal-path]
                                 [--base-url http://127.0.0.1:8000]
                                 [--skip-conformance] [--skip-chat]

Exit code 0 if every check PASSes or cleanly SKIPs, 1 if any FAILs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

RL_DIR = Path(__file__).parent
sys.path.insert(0, str(RL_DIR))

import config  # noqa: E402
import cross_corpus  # noqa: E402

STAGE2_DIR = RL_DIR.parent / "Stage 2"


class Result:
    def __init__(self, name: str):
        self.name = name
        self.status = "FAIL"
        self.detail = ""

    def ok(self, detail: str = "") -> "Result":
        self.status, self.detail = "PASS", detail
        return self

    def fail(self, detail: str = "") -> "Result":
        self.status, self.detail = "FAIL", detail
        return self

    def skip(self, detail: str = "") -> "Result":
        self.status, self.detail = "SKIP", detail
        return self


def _patient_dir(patient_corpus_id: str) -> Path:
    return STAGE2_DIR / "data" / patient_corpus_id


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug or "customer"


# ── 1. Reverse-map integrity ────────────────────────────────────────────────

def check_reverse_map_integrity() -> Result:
    r = Result("reverse_map_integrity")
    try:
        rev = cross_corpus._get_topic_reverse_map()
        filtered = json.loads((config.STAGE1_OUTPUT / "filtered_chunks.json").read_text(encoding="utf-8"))
        all_ids = [str(c["chunk_id"]) for c in filtered]
        orphans = [cid for cid in all_ids if cid not in rev]
        if orphans:
            return r.fail(f"{len(orphans)}/{len(all_ids)} chunk_ids have no owning topic (sample: {orphans[:5]})")
        return r.ok(f"{len(all_ids)} chunk_ids -> {len(set(rev.values()))} topics, 0 orphans")
    except Exception as e:
        return r.fail(f"exception: {e}")


# ── 2. Cross-corpus boundary spike (+ pick a real matched topic) ───────────

def check_boundary_spike(patient_corpus_id: str) -> "tuple[Result, Optional[str], Optional[str]]":
    r = Result("cross_corpus_boundary_spike")
    registry_path = _patient_dir(patient_corpus_id) / "output" / "topic_registry.csv"
    if not registry_path.exists():
        return r.fail(f"no topic_registry.csv for patient corpus '{patient_corpus_id}' — process it first"), None, None

    import pandas as pd
    df = pd.read_csv(registry_path)
    queries = []
    query_by_label = {}
    for _, row in df.iterrows():
        label = row.get("master_label", "")
        query = row.get("grounded_summary") or row.get("summarized_description") or ""
        if label and isinstance(query, str) and query:
            queries.append({"id": label, "query": query})
            query_by_label[label] = query
    if not queries:
        return r.fail("patient corpus has no topics with a summary to query with"), None, None

    with tempfile.TemporaryDirectory() as tmp:
        in_path, out_path = Path(tmp) / "queries.json", Path(tmp) / "results.json"
        in_path.write_text(json.dumps(queries), encoding="utf-8")

        # Deliberately poison the env the way Stage 2's real pipeline does,
        # to prove the CLI's env-stripping still resolves the GUIDELINE
        # corpus, not the patient corpus, under exactly the adversarial
        # condition this design exists to guard against.
        patient_dir = _patient_dir(patient_corpus_id)
        poisoned_env = {
            **os.environ,
            "STAGE1_PDF_DIR": str(patient_dir / "pdfs"),
            "STAGE1_OUTPUT_DIR": str(patient_dir / "output"),
            "CHROMA_DIR_OVERRIDE": str(patient_dir / "chroma_db"),
            "INDEX_DIR_OVERRIDE": str(patient_dir / "index"),
            "STAGE2_PATIENT_ID": patient_corpus_id,
        }
        proc = subprocess.run(
            [sys.executable, str(RL_DIR / "cross_corpus_cli.py"),
             "--in", str(in_path), "--out", str(out_path), "--k", "3"],
            cwd=str(RL_DIR), env=poisoned_env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return r.fail(f"cross_corpus_cli.py exited {proc.returncode}: {proc.stderr[-1000:]}"), None, None
        payload = json.loads(out_path.read_text(encoding="utf-8"))

    if payload.get("guideline_kb_version") != cross_corpus.guideline_kb_version():
        return r.fail("guideline_kb_version mismatch — CLI resolved a DIFFERENT guideline KB "
                       "than this process (the exact bug the env-stripping guards against)"), None, None

    best_topic, best_score, n_matched = None, float("-inf"), 0
    for topic_label, matches in payload["results"].items():
        if matches:
            n_matched += 1
            if matches[0]["score"] > best_score:
                best_topic, best_score = topic_label, matches[0]["score"]

    if not best_topic:
        return r.fail(f"0/{len(queries)} patient topics matched any guideline topic "
                       f"(min_score={cross_corpus.MIN_MATCH_SCORE})"), None, None

    return r.ok(
        f"{n_matched}/{len(queries)} topics cleared min_score={cross_corpus.MIN_MATCH_SCORE}; "
        f"best='{best_topic}' (score={best_score:.3f}); kb_version confirmed correct under poisoned env"
    ), best_topic, query_by_label[best_topic]


# ── 3/4. Live endpoints ──────────────────────────────────────────────────────

def check_enhanced_summary(base_url: str, patient_corpus_id: str, topic_label: str) -> Result:
    r = Result("enhanced_summary_endpoint")
    import requests
    try:
        resp = requests.get(
            f"{base_url}/api/corpora/patients/{patient_corpus_id}/topics/{_slugify(topic_label)}/enhanced-summary",
            timeout=90,
        )
    except requests.exceptions.ConnectionError:
        return r.skip(f"dev server unreachable at {base_url}")
    if resp.status_code != 200:
        return r.fail(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if not data.get("merged_summary"):
        return r.fail("empty merged_summary in response")
    return r.ok(
        f"merged_summary ({len(data['merged_summary'])} chars), "
        f"{len(data.get('matched_guideline_topics', []))} guideline match(es), "
        f"from_cache={data.get('from_cache')}"
    )


def check_guideline_conformance(base_url: str, patient_corpus_id: str, topic_label: str) -> Result:
    r = Result("guideline_conformance_endpoint")
    import requests
    try:
        resp = requests.get(
            f"{base_url}/api/corpora/patients/{patient_corpus_id}/topics/"
            f"{_slugify(topic_label)}/guideline-conformance",
            timeout=180,
        )
    except requests.exceptions.ConnectionError:
        return r.skip(f"dev server unreachable at {base_url}")
    if resp.status_code != 200:
        return r.fail(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    valid_types = {
        "concordant with guideline", "deviates — clinically significant",
        "deviates — borderline", "guideline silent on this case",
    }
    bad = [d["change_type"] for d in data.get("delta", []) if (d.get("change_type") or "").lower() not in valid_types]
    if data.get("matches", 0) > 0 and not data.get("delta"):
        return r.fail("matches>0 but delta list is empty")
    if bad:
        return r.fail(f"unrecognized change_type value(s): {bad}")
    return r.ok(
        f"{data.get('matches', 0)} match(es), {len(data.get('evolution', []))} evolution card(s), "
        f"from_cache={data.get('from_cache')}"
    )


# ── 5. Chat two-hop ──────────────────────────────────────────────────────────

def check_chat_two_hop(patient_corpus_id: str, topic_label: str, topic_query_text: str) -> Result:
    r = Result("chat_two_hop")
    try:
        import corpus_registry
        import retriever
        patient_dir = _patient_dir(patient_corpus_id)
        corpus_registry.register(patient_corpus_id, patient_dir / "chroma_db", patient_dir / "index")

        # Built from the patient topic's OWN real summary text (the same
        # text the boundary spike already used), not just the bare topic
        # label — a bare label wrapped in a generic template scores poorly
        # against the patient's own corpus (weak hop-1 grounding), which
        # now correctly makes hop 2 skip rather than fire on a bad query.
        # Real patient content, plus a phrase from _PATIENT_ANALYSIS_SIGNALS,
        # reliably grounds hop 1 well enough to exercise hop 2 for real.
        query = f"{topic_query_text[:300]} — is this abnormal, what is the clinical significance?"
        result = retriever.retrieve(query, corpus_id=patient_corpus_id)
        # Untagged ("" _corpus_id) means hop 2 never fired — those chunks
        # are still hop 1's own patient-corpus result, just never routed
        # through the cross-corpus tagging/merge step.
        corpora_seen = {c.get("_corpus_id") or patient_corpus_id for c in result["chunks"]}
        if patient_corpus_id not in corpora_seen:
            return r.fail(f"hop 1 (patient corpus) never appeared in merged result — corpora seen: {corpora_seen}")
        if "guidelines" not in corpora_seen:
            return r.fail(
                f"hop 2 (guidelines) never fired even with real patient content as the query — corpora seen: "
                f"{corpora_seen}. (hop-1 top score may be below config.CONFIDENCE_THRESHOLD)"
            )
        return r.ok(f"{len(result['chunks'])} chunks, corpora represented: {sorted(corpora_seen)}")
    except Exception as e:
        return r.fail(f"exception: {e}")


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient-corpus-id", default="dr-lal-path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-conformance", action="store_true", help="skip the LLM-heavy delta+evolution check")
    parser.add_argument("--skip-chat", action="store_true")
    args = parser.parse_args()

    results: List[Result] = [check_reverse_map_integrity()]

    spike_result, best_topic, best_query = check_boundary_spike(args.patient_corpus_id)
    results.append(spike_result)

    if best_topic:
        results.append(check_enhanced_summary(args.base_url, args.patient_corpus_id, best_topic))
        if not args.skip_conformance:
            results.append(check_guideline_conformance(args.base_url, args.patient_corpus_id, best_topic))
        if not args.skip_chat:
            results.append(check_chat_two_hop(args.patient_corpus_id, best_topic, best_query))
    else:
        results.append(Result("enhanced_summary_endpoint").skip("no matched topic from boundary spike"))

    print("\n" + "=" * 78)
    print(f"SMOKE TEST — patient corpus: {args.patient_corpus_id!r}")
    print("=" * 78)
    icons = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}
    for res in results:
        print(f"[{icons[res.status]:4s}] {res.name:32s} {res.detail}")
    print("=" * 78)

    n_fail = sum(1 for res in results if res.status == "FAIL")
    if n_fail:
        print(f"\n{n_fail} check(s) FAILED.")
        sys.exit(1)
    print("\nAll checks passed (or cleanly skipped).")


if __name__ == "__main__":
    main()
