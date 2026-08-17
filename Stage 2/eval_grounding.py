"""
Stage 2/eval_grounding.py
─────────────────────────
Evaluation harness for the Patient–Guideline Semantic Enrichment Engine
(Stage 2/guideline_grounding.py). Exercises the six §34 semantic cases end to
end — retrieval → LLM classification → deterministic context validation —
against the REAL guideline hierarchy index, with CONTROLLED patient contexts
so each case's expected outcome is knowable in advance.

The six cases:
  1. DIRECT_MATCH      — Lipid Panel Measurement Protocol ↔ Lipid Measurement Guideline Update
  2. PARTIAL_MATCH     — Lipid Panel Measurement Protocol ↔ cardiovascular risk assessment
  3. CONTEXTUAL_MATCH  — Kidney Function Assessment ↔ CKD monitoring in diabetes standards,
                          diabetes PRESENT elsewhere in the patient universe → accepted
  4. CONTEXTUAL→NO_MATCH — same as 3, but diabetes ABSENT → deterministically rejected
  5. NO_MATCH (domain) — Lipid Panel Measurement Protocol ↔ lipid management in pregnancy
                          (different subject, pregnancy context absent)
  6. NO_MATCH (keyword) — Lipid Treatment Thresholds ↔ Dyslipidemia Treatment Guideline
                          (same keywords, wrong meaning — lab reference values ≠ treatment subject)

Usage:
    cd "Stage 2"
    STAGE2_PATIENT_ID=dr-lalpath-labs <Stage 1 venv python> eval_grounding.py
    STAGE2_PATIENT_ID=dr-lalpath-labs <Stage 1 venv python> eval_grounding.py --skip-llm
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage2_config as s2cfg  # noqa: E402
import guideline_grounding as gg  # noqa: E402

s2cfg.setup_patient_env("dr-lalpath-labs")
s2cfg.put_stage1_first_on_path()

import config  # noqa: E402
import llm_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("eval_grounding")

GUIDELINE_HIERARCHY_DIR = Path(s2cfg.GUIDELINES_OUTPUT_DIR) / "hierarchy_summaries"

# Real patient topic bodies (verbatim from the wm17s_pdf topic summaries).
LIPID_PANEL_BODY = """**Patient Result:** 100.00 mg/dL (Total Cholesterol), 100.00 mg/dL (Triglycerides), >200 mg/dL (HDL Cholesterol) (ref: Total Cholesterol <200.00 mg/dL, Triglycerides <150.00 mg/dL, HDL Cholesterol >40.00 mg/dL)

**Clinical Interpretation:** Patient's Total Cholesterol (100.00 mg/dL) and Triglycerides (100.00 mg/dL) are within normal range, while HDL Cholesterol (>200 mg/dL) is elevated per guideline thresholds.

**Significance Level:** MODERATE

**Clinical Recommendation:** Lipid Panel Measurement Protocol + Serial Sampling & Additional Testing

**Key Clinical Actions:**
- Three serial samples 3 weeks apart are recommended for Total Cholesterol, Triglycerides, HDL & LDL Cholesterol to account for physiological and analytical variations.
- Additional testing for Apolipoprotein B, hsCRP, Lp(a), and LP-PLA2 should be considered among patients with moderate risk for ASCVD for risk refinement.

**Patient Preparation / Diagnostic Criteria:**
- Physiological and analytical variations necessitate serial sampling for accurate lipid measurement.
- Fasting for at least 9-12 hours prior to lipid panel measurement to minimize dietary influences on triglycerides and other lipid fractions."""

KIDNEY_BODY = """**Patient Result:** 1.00 mg/dL (ref: 0.70 - 1.30 mg/dL)

**Clinical Interpretation:** Patient's creatinine level of 1.00 mg/dL falls within the normal reference range (0.70 - 1.30 mg/dL), indicating no immediate concern for kidney dysfunction in the context of dyslipidemia management.

**Significance Level:** MODERATE

**Clinical Recommendation:** Kidney Function Assessment in Dyslipidemia Management

**Key Clinical Actions:**
- Assess kidney function (e.g., serum creatinine) as part of cardiovascular risk stratification in dyslipidemia management.
- Perform serum creatinine testing using standardized methods (e.g., Modified Jaffe, Kinetic) to evaluate kidney function.
- Unexpected or alarming kidney function results should prompt immediate patient contact for remedial action.

**Patient Preparation / Diagnostic Criteria:**
- Serum creatinine must be measured using validated laboratory methods (e.g., Modified Jaffe, Kinetic) for accurate kidney function assessment."""

LIPID_THRESHOLDS_BODY = """**Significance Level:** HIGH

**Clinical Recommendation:** Lipid Treatment Thresholds for Extreme/Very High Risk Groups

**Key Clinical Actions:**
- For Extreme Risk Group Category A, LDL-C treatment goal is <50 mg/dL (optional ≤30 mg/dL) and non-HDL-C goal is <80 mg/dL (optional ≤60 mg/dL).
- For Very High Risk, LDL-C treatment goal is <50 mg/dL and non-HDL-C goal is <80 mg/dL.
- Therapy is considered when LDL-C ≥50 mg/dL or non-HDL-C ≥80 mg/dL in both Extreme and Very High Risk groups."""


def _ptopic(label: str, body: str) -> dict:
    """Synthetic patient topic dict matching guideline_grounding's schema."""
    return {
        "master_label": label,
        "source_docs": ["WM17S.pdf"],
        "file": "",
        "body": body,
        "result": "",
        "interpretation": "",
        "significance": "MODERATE",
        "recommendation": "",
        "actions": [],
        "preparation": [],
        "description": "",
        "keywords": "",
        "summarized_description": "",
        "grounded_summary": "",
        "qna": "",
        "is_boilerplate": False,
    }


def _context(**states) -> dict:
    """Controlled patient context index — every concept defaults to UNKNOWN."""
    return {c: states.get(c, "UNKNOWN") for c in gg.CONTEXT_CONCEPTS}


def _run_case(guideline_index, topic, context_index, target_label, expected):
    """Retrieve → classify → validate a single target. Returns a result dict."""
    candidates = gg.retrieve_guideline_topic_candidates(topic, guideline_index)
    cands = candidates[:12]
    retrieved = any(c["master_label"] == target_label for c in cands)
    if not retrieved:
        from copy import deepcopy
        injected = dict(deepcopy(guideline_index[target_label]))
        injected.update({
            "master_label": target_label,
            "local_score": None,
            "rerank_score": None,
            "chunk_id": None,
            "chunk_text": None,
            "grounded_summary": None,
            "summarized_description": None,
        })
        cands = [injected] + cands

    prompt = gg._build_classify_prompt(
        topic, cands, gg.render_context_index_summary(context_index))
    raw = llm_client.generate_batch(
        [prompt], max_tokens=2600, desc="Grounding eval — classification",
        system_prompt=gg._CLASSIFY_SYSTEM, stop=None, enable_thinking=False)[0]
    cl = gg._parse_classify_output(raw, cands)
    pre = cl.get(target_label, {}).get("status", "NO_MATCH")
    cl = gg.validate_patient_context(cl, context_index)
    entry = cl.get(target_label, {})
    post = entry.get("status", "NO_MATCH")

    return {
        "case": expected["case"],
        "patient_topic": topic["master_label"],
        "guideline_topic": target_label,
        "expected": expected["status"],
        "pre_validation": pre,
        "post_validation": post,
        "validated_context": entry.get("validated_context", []),
        "rejected_context": entry.get("rejected_context", []),
        "required_context": entry.get("required_context", []),
        "match_reason": entry.get("match_reason", ""),
        "retrieved": retrieved,
        "passed": post == expected["status"],
        "extra": expected.get("extra"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Patient–Guideline grounding evaluation harness")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Run retrieval only; skip LLM classification (marks cases SKIPPED)")
    args = parser.parse_args()

    guideline_index = gg._parse_guideline_hierarchy_files(GUIDELINE_HIERARCHY_DIR)
    if not guideline_index:
        log.error(f"No guideline topics parsed from {GUIDELINE_HIERARCHY_DIR}")
        sys.exit(1)

    lipid_panel = _ptopic("Lipid Panel Measurement Protocol", LIPID_PANEL_BODY)
    kidney = _ptopic("Kidney Function Assessment", KIDNEY_BODY)
    lipid_thresholds = _ptopic("Lipid Treatment Thresholds", LIPID_THRESHOLDS_BODY)

    cases = [
        (lipid_panel,
         _context(),
         "Lipid Measurement Guideline Update",
         {"case": 1, "status": "DIRECT_MATCH"}),
        (kidney,
         _context(ckd="PRESENT"),
         "point-of-care kidney function testing",
         {"case": 2, "status": "PARTIAL_MATCH",
          "extra": {"require_validated": []}}),
        (kidney,
         _context(diabetes="PRESENT"),
         "CKD monitoring in diabetes standards",
         {"case": 3, "status": "CONTEXTUAL_MATCH",
          "extra": {"require_validated": ["diabetes"]}}),
        (kidney,
         _context(diabetes="ABSENT"),
         "CKD monitoring in diabetes standards",
         {"case": 4, "status": "NO_MATCH",
          "extra": {"require_absent": ["diabetes"]}}),
        (lipid_panel,
         _context(),
         "lipid management in pregnancy",
         {"case": 5, "status": "NO_MATCH"}),
        (lipid_panel,
         _context(),
         "Dyslipidemia Treatment Guideline",
         {"case": 6, "status": "NO_MATCH"}),
    ]

    results = []
    for topic, ctx, target, expected in cases:
        if args.skip_llm:
            results.append({
                "case": expected["case"], "patient_topic": topic["master_label"],
                "guideline_topic": target, "expected": expected["status"],
                "status": "SKIPPED", "passed": None,
            })
            continue
        results.append(_run_case(guideline_index, topic, ctx, target, expected))

    print()
    print(f"{'Case':<5}{'Expected':<18}{'Final':<18}{'Pre-valid':<18}{'Retrieved':<10}Result")
    print("-" * 84)
    n_pass = 0
    for r in results:
        if r.get("passed") is None:
            mark, flag = "SKIPPED", "  ~"
        else:
            n_pass += int(r["passed"])
            mark, flag = ("PASS" if r["passed"] else "FAIL"), ("  ✓" if r["passed"] else "  ✗")
        print(f"{r['case']:<5}{r['expected']:<18}{r.get('post_validation', r.get('status', '')):<18}"
              f"{r.get('pre_validation', ''):<18}{str(r.get('retrieved', '')):<10}{mark}{flag}")
        if r.get("match_reason") and r.get("passed") is False:
            print(f"      reason: {r['match_reason'][:160]}")
    print("-" * 84)

    # Extra semantic checks (cases 2, 3 & 4) — validation correctness, not just final status.
    if not args.skip_llm:
        for r in results:
            extra = r.get("extra") or {}
            if "require_validated" in extra:
                missing = [c for c in extra["require_validated"] if c not in r["validated_context"]]
                ok = not missing and r["passed"]
                if extra.get("strict") and r["pre_validation"] != "CONTEXTUAL_MATCH":
                    ok = False
                r["passed"] = bool(ok and r["passed"])
                print(f"Case {r['case']} validated-context check: "
                      f"{'PASS ✓' if ok else 'FAIL ✗'} (validated={r['validated_context']})")
            if "require_absent" in extra:
                absent = [c for c in extra["require_absent"] if c in r["validated_context"]]
                ok = not absent and r["passed"]
                r["passed"] = bool(ok and r["passed"])
                print(f"Case {r['case']} absent-context check: "
                      f"{'PASS ✓' if ok else 'FAIL ✗'} (validated={r['validated_context']})")

    passed = sum(1 for r in results if r.get("passed") is True)
    failed = sum(1 for r in results if r.get("passed") is False)
    print(f"\nResult: {passed} passed, {failed} failed"
          + (f", {sum(1 for r in results if r.get('passed') is None)} skipped" if args.skip_llm else ""))

    report = Path("Stage 2") / "eval_grounding_report.json"
    report = Path(__file__).parent / "eval_grounding_report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Report → {report}")


if __name__ == "__main__":
    main()