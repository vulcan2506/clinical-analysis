"""
evolution_analyzer.py
──────────────────────
Value-add / evolution synthesis pass, built on top of delta_analyzer.py's
output. Where the delta report asks "what changed" (and is neutral about
whether a change is good, bad, or a contradiction), this pass asks a
narrower, constructive question: for features that genuinely evolved for
the better, what foundation did the older version lay, and what value did
the newer version build on top of it?

INPUT
─────
Reads data/output/delta_jobs_cache.json (produced by delta_analyzer.py) —
no raw chunk re-reading, no new profile extraction. Each cached job already
has profile_A, profile_B, and a scored/classified delta.

FILTER
──────
"Constructive" is domain-agnostic by EXCLUSION, not a hardcoded whitelist:
a job is skipped only if its change_type carries a non-constructive SIGNAL
(reversal/contradiction, no-change/unrelated, deprecation/removal, or a
purely-administrative edit) — see _NON_CONSTRUCTIVE_SIGNALS. Everything else
is treated as a candidate value-add story. An earlier version of this filter
was a fixed whitelist of software-release category names (Minor Enhancement,
New Requirement, Workflow Automation, Bug Fix) — that produced ZERO matches
on a medical-coding corpus, whose delta_analyzer.py classifies changes into
entirely different, domain-native category names.

SCOPE NOTE
──────────
This produces one card per topic per adjacent version-pair job — today
that means a single hop (v25.2 -> v26.1), since that's the only real pair
on disk. The schema and cache are shaped so that when a third real version
document lands, the same per-pair card generation runs again on the new
pair; a follow-on "timeline compiler" that chains cards across 3+ versions
into a multi-hop narrative is deliberately NOT built here — there's nothing
to chain yet, and building it now would be speculative scaffolding.
"""

import json
import logging
import re
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

import config
import llm_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

DELTA_JOBS_CACHE_PATH = config.OUTPUT_DIR / "delta_jobs_cache.json"
EVOLUTION_CACHE_PATH  = config.OUTPUT_DIR / "evolution_cards_cache.json"
REPORT_MD_PATH        = config.OUTPUT_DIR / "version_evolution_report.md"

# Signals about the NATURE of a change (not domain vocabulary) that mark a
# job as NOT a value-add story — everything else is a candidate. Deliberately
# an exclude-list, not an include-whitelist: an include-whitelist has to
# predict every domain's own category names in advance (impossible), while
# these signal words describe change nature and hold up across domains.
_NON_CONSTRUCTIVE_SIGNALS = (
    "no change", "unrelated", "mismatch", "weak overlap", "reversal",
    "contradiction", "deprecat", "removal", "no meaningful", "administrative",
)


def _is_constructive(change_type: str) -> bool:
    ct = (change_type or "").lower()
    return not any(sig in ct for sig in _NON_CONSTRUCTIVE_SIGNALS)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

class EvolutionCard(BaseModel):
    feature_name:    str       = Field(description="Short descriptive name of the feature.")
    foundation:      str       = Field(description="What the older version established — one sentence.")
    value_added:     List[str] = Field(description="Concrete capabilities/value the newer version builds on top, max 5.")
    narrative:       str       = Field(description="2-3 sentences: older version introduced X; newer version builds on it by Y, enabling Z.")
    change_type:     str       = Field(default="")
    builds_on_prior: bool      = Field(default=True)
    # Clinical fusion fields (only populated in patient-vs-guideline fusion mode)
    clinical_finding:     Optional[str] = Field(default=None, description="What the patient's lab result shows — e.g. 'B12=200 pg/mL (below ref 211-911)'")
    guideline_context:    Optional[str] = Field(default=None, description="What the clinical guideline says about this finding.")
    clinical_significance: Optional[str] = Field(default=None, description="Why this matters — concordant with guideline, deviates, etc.")


def unload():
    pass


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════════════════════

_EVOLUTION_PROMPT = """\
You are {analyst_role} writing a "value-add" note about a change in \
{doc_purpose} for the feature "{topic}". You already have structured \
profiles for both versions and a \
classified delta below — do NOT re-read raw text, just re-synthesize \
these facts into a constructive evolution narrative.

CRITICAL RULES:
- Output ONLY a single JSON object — nothing before or after it.
- Ground every statement in the profiles/delta below — do not invent facts.
- Max 5 items in value_added. Close all braces.

{clinical_rules}

Output this exact structure with real values:

{{{{
  "feature_name": "<short descriptive name>",
  "foundation": "<one sentence: what {vA} established>",
  "value_added": [
    "<concrete capability {vB} adds on top of that foundation>",
    "<another — max 5 total>"
  ],
  "narrative": "<2-3 sentences: {vA} introduced X; {vB} builds on it by Y, enabling Z>"{clinical_fields}
}}}

### EXAMPLE ###

TOPIC: Service Definition Split Optimization
{vA} profile:
  Feature: Service Definition Split Handling
  Behaviors: Full evaluation completes before applying splits; splits only applied if they affect outcome

{vB} profile:
  Feature: Service Definition Split Optimization
  Behaviors: Optimization override scenarios handled correctly; performance metrics available
  Requirements: SERVICE_DEFINITION_EVALUATOR_OPTIMIZATION_ENABLED (default: true); no restart needed

Delta analysis: {vA} improved split handling so full evaluation completes before splits are applied. \
{vB} extends this with a runtime property controlling expression reordering for performance, and fixes \
override handling gaps. The core {vA} behavior is preserved and extended.

OUTPUT:
{{{{
  "feature_name": "Service Definition Split Optimization",
  "foundation": "{vA} established correct split handling by completing full evaluation before applying splits.",
  "value_added": [
    "SERVICE_DEFINITION_EVALUATOR_OPTIMIZATION_ENABLED property adds configurable performance optimization on top of the existing evaluation logic",
    "Override scenarios that previously had gaps are now handled correctly",
    "Optional performance metrics collection was added for visibility into evaluation cost"
  ],
  "narrative": "{vA} introduced correct, if unoptimized, split-handling logic. {vB} builds directly on that foundation by adding a configurable optimization path and fixing override edge cases, giving teams a faster evaluator without losing any of the correctness {vA} established."
}}}

### ACTUAL TASK ###
{clinical_example}
TOPIC: {topic}

{vA} profile:
{profile_A}

{vB} profile:
{profile_B}

Delta analysis: {delta_analysis}
Key differences: {key_differences}

OUTPUT (JSON only — close all braces, max 5 items in value_added):"""


def _profile_to_text(p: dict) -> str:
    lines = [f"Feature: {p.get('feature_name', '')}"]
    # Clinical fields — prominent placement when present
    pv = p.get("patient_value")
    rr = p.get("reference_range")
    interp = p.get("interpretation")
    if pv or interp:
        clinical_bits = []
        if pv:
            clinical_bits.append(f"Patient value: {pv}")
        if rr:
            clinical_bits.append(f"Reference range: {rr}")
        if interp:
            clinical_bits.append(f"Interpretation: {interp}")
        lines.append("Clinical: " + " | ".join(clinical_bits))
    if p.get("key_behaviors"):
        lines.append("Behaviors: " + " | ".join(p["key_behaviors"]))
    if p.get("requirements"):
        lines.append("Requirements: " + " | ".join(p["requirements"]))
    if p.get("new_items"):
        lines.append("New items: " + " | ".join(p["new_items"]))
    return "\n  ".join(lines)


def _build_dynamic_example_block(profile: dict) -> Optional[str]:
    shot = profile.get("evolution_few_shot") or {}
    topic       = shot.get("topic")
    foundation  = shot.get("foundation")
    narrative   = shot.get("narrative")
    value_added = shot.get("value_added") or []
    if not (topic and foundation and narrative and value_added):
        return None

    value_added_json = ",\n    ".join(json.dumps(v) for v in value_added[:5])
    # Quadruple braces: this block gets spliced into _EVOLUTION_PROMPT, which
    # itself undergoes a LATER .format() call in _build_prompt — {{{{ -> {{
    # here, then {{ -> { at that call, so the JSON braces survive as literal.
    return f"""### EXAMPLE ###

TOPIC: {topic}

OUTPUT:
{{{{
  "feature_name": {json.dumps(topic)},
  "foundation": {json.dumps(foundation)},
  "value_added": [
    {value_added_json}
  ],
  "narrative": {json.dumps(narrative)}
}}}}
"""


def _get_evolution_prompt_template(profile: Optional[dict]) -> str:
    """Saved-on-disk > dynamic (from profile.evolution_few_shot) > static fallback."""
    if not profile:
        return _EVOLUTION_PROMPT
    type_key = profile.get("type_key", "")

    import context_profiler
    if type_key:
        saved = context_profiler.load_prompt(type_key, "evolution")
        if saved:
            return saved

    block = _build_dynamic_example_block(profile)
    if not block:
        return _EVOLUTION_PROMPT

    template = _EVOLUTION_PROMPT
    start = template.find("### EXAMPLE ###")
    end = template.find("### ACTUAL TASK ###")
    if start != -1 and end != -1:
        template = template[:start] + block + "\n" + template[end:]

    if type_key:
        context_profiler.save_prompt(type_key, "evolution", template)
    return template


def _build_prompt(job: Dict, analyst_role: str, doc_purpose: str) -> str:
    d = job["delta"]
    template = _get_evolution_prompt_template(job.get("_profile"))

    is_fusion = job.get("_fusion_mode")

    if is_fusion:
        # User-selected framing for patient-vs-guideline fusion: the
        # guideline is the established baseline ("foundation"), the
        # patient's specific finding is what builds on top of it ("value
        # added") — inverted relative to fusion delta jobs' own
        # vA=Patient/vB=Guideline convention (kept as-is in the job dict so
        # delta and evolution can share the exact same job list; swapped
        # only here, at render time, for evolution's foundation/value-added
        # framing specifically).
        vA, vB = job["vB"], job["vA"]
        profile_A, profile_B = job["profile_B"], job["profile_A"]
    else:
        vA, vB = job["vA"], job["vB"]
        profile_A, profile_B = job["profile_A"], job["profile_B"]

    # Clinical fusion extras — empty strings when not in fusion mode so
    # the template still formats cleanly.
    clinical_rules = ""
    clinical_fields = ""
    clinical_example = ""
    if is_fusion:
        clinical_rules = (
            "- When in clinical fusion mode, you MUST also output these three fields:\n"
            '  "clinical_finding": "<what the patient\'s lab result shows — include the actual value, '
            'units, and reference range if available from the profile, e.g. \'Vitamin B12 = 200 pg/mL '
            '(below reference 211–911 pg/mL)\' or \'Vitamin D = 150 nmol/L (within reference 75–250 nmol/L)\' '
            '— use null only if the profile has no patient_value/interpretation>,"\n'
            '  "guideline_context": "<what the clinical guideline recommends or states about this '
            'parameter — one sentence>,"\n'
            '  "clinical_significance": "<1-2 sentences: is the patient finding concordant with, '
            'deviating from, or silent on the guideline? What clinical action (if any) does this imply?>"\n'
        )
        clinical_fields = ',\n  "clinical_finding": "...",\n  "guideline_context": "...",\n  "clinical_significance": "..."'
        clinical_example = (
            "### CLINICAL FUSION EXAMPLE ###\n"
            "\n"
            "TOPIC: Vitamin B12\n"
            "{vA} profile (Guideline — established standard):\n"
            "  Feature: Serum Vitamin B12 Assessment\n"
            "  Behaviors: Serum B12 is a first-line test for B12 status; values below 211 pg/mL "
            "indicate deficiency; methylmalonic acid or homocysteine used to confirm borderline results\n"
            "\n"
            "{vB} profile (Patient — specific finding):\n"
            "  Feature: Patient Vitamin B12 Result\n"
            "  Clinical: Patient value: 200 pg/mL | Reference range: 211–911 pg/mL | "
            "Interpretation: Below reference range — consistent with B12 deficiency\n"
            "  Behaviors: Serum B12 level measured as part of routine workup\n"
            "\n"
            "Delta analysis: The guideline establishes B12 < 211 pg/mL as the deficiency threshold. "
            "The patient's result (200 pg/mL) falls below this threshold, confirming deficiency.\n"
            "\n"
            "OUTPUT:\n"
            "{{{{\n"
            '  "feature_name": "Vitamin B12 — Guideline vs Patient Finding",\n'
            '  "foundation": "The clinical guideline establishes serum B12 < 211 pg/mL as the '
            'diagnostic threshold for deficiency, with MMA/homocysteine as confirmatory tests.",\n'
            '  "value_added": [\n'
            '    "Patient\'s B12 result (200 pg/mL) falls below the guideline threshold, '
            'confirming deficiency status",\n'
            '    "Result is 11 pg/mL below the lower reference limit — subclinical but clinically '
            'significant",\n'
            '    "Guideline recommends confirming with MMA or homocysteine before initiating treatment"\n'
            '  ],\n'
            '  "narrative": "The guideline sets the diagnostic framework for B12 deficiency at <211 pg/mL. '
            'The patient\'s result of 200 pg/mL falls below this threshold, confirming deficiency per '
            'guideline criteria. Confirmatory testing with MMA or homocysteine is recommended before '
            'initiating B12 replacement therapy.",\n'
            '  "clinical_finding": "Vitamin B12 = 200 pg/mL (below reference 211–911 pg/mL) — '
            'consistent with B12 deficiency",\n'
            '  "guideline_context": "Serum B12 < 211 pg/mL indicates deficiency; confirm with MMA or '
            'homocysteine before treatment.",\n'
            '  "clinical_significance": "Patient finding is concordant with guideline — B12 level is '
            'below the deficiency threshold. Confirmatory testing and B12 replacement therapy should '
            'be considered."\n'
            "}}}}\n"
        ).format(vA=vA, vB=vB)

    return template.format(
        analyst_role=analyst_role,
        doc_purpose=doc_purpose,
        topic=job["topic"],
        vA=vA,
        vB=vB,
        profile_A=_profile_to_text(profile_A),
        profile_B=_profile_to_text(profile_B),
        delta_analysis=d.get("analysis", ""),
        key_differences=" | ".join(d.get("key_differences", [])),
        clinical_rules=clinical_rules,
        clinical_fields=clinical_fields,
        clinical_example=clinical_example,
    )


# ══════════════════════════════════════════════════════════════════════════════
# JSON PARSING (same robust cascade approach as delta_analyzer._extract_json_block)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_json_block(raw: str) -> Optional[dict]:
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    for attempt in [cleaned, cleaned.split('\n\n')[0]]:
        try:
            return json.loads(attempt)
        except Exception:
            pass

    start = cleaned.find('{')
    if start == -1:
        return None
    content = cleaned[start:]

    stack: List[str] = []
    in_str = escape = False
    complete_end = -1
    for i, ch in enumerate(content):
        if escape:
            escape = False; continue
        if ch == '\\' and in_str:
            escape = True; continue
        if ch == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack:
                stack.pop()
                if not stack:
                    complete_end = i
                    break
    if complete_end != -1:
        try:
            return json.loads(content[:complete_end + 1])
        except Exception:
            pass

    if stack:
        closings = ''.join(']' if b == '[' else '}' for b in reversed(stack))
        base = content.rstrip()
        candidates = [
            base,
            re.sub(r',?\s*"[^"]*$', '', base),
            re.sub(r',\s*$', '', base),
            re.sub(r',?\s*"[^"]*$', '', re.sub(r',\s*$', '', base)),
        ]
        for candidate in candidates:
            for suffix in [closings, closings.rstrip('}') + '}']:
                try:
                    return json.loads(candidate + suffix)
                except Exception:
                    pass
    return None


def _parse_card(raw: str, job: Dict) -> Optional[EvolutionCard]:
    data = _extract_json_block(raw)
    if not data:
        log.warning(f"Could not parse evolution card for '{job['topic']}' — skipping.")
        return None
    try:
        return EvolutionCard(
            feature_name=data.get("feature_name", job["topic"]),
            foundation=data.get("foundation", ""),
            value_added=(data.get("value_added") or [])[:5],
            narrative=data.get("narrative", ""),
            change_type=job["delta"]["change_type"],
            builds_on_prior=True,
            clinical_finding=data.get("clinical_finding"),
            guideline_context=data.get("guideline_context"),
            clinical_significance=data.get("clinical_significance"),
        )
    except Exception:
        log.warning(f"Could not construct EvolutionCard for '{job['topic']}' — skipping.")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# REPORT RENDERING
# ══════════════════════════════════════════════════════════════════════════════

def _render_card(idx: int, job: Dict, card: EvolutionCard) -> List[str]:
    is_fusion = job.get("_fusion_mode")
    md = []
    md.append(f"### {idx}. {card.feature_name}")
    md.append(f"**Location:** {job['parent']} → {job['sub']}  ")
    md.append(f"**Appears in:** {job['vA']} → {job['vB']}  ")
    md.append(f"**Change type:** {card.change_type}\n")

    if is_fusion and card.clinical_finding:
        # Clinical fusion rendering — patient value and guideline context
        # are the most important information, placed prominently at top.
        md.append(f"**Patient Finding:** {card.clinical_finding}\n")
        md.append(f"**Guideline Standard ({job['vA']}):** {card.foundation}\n")
        md.append(f"**Key Points:**")
        for v in card.value_added:
            md.append(f"- {v}")
        md.append("")
        md.append(f"**Clinical Significance:** {card.clinical_significance}\n")
        md.append(f"**Guideline Context:** {card.guideline_context}\n")
        md.append(f"**Narrative:** {card.narrative}\n")
    else:
        # Version-to-version rendering (original)
        md.append(f"**Foundation ({job['vA']}):** {card.foundation}\n")
        md.append(f"**Value added ({job['vB']}):**")
        for v in card.value_added:
            md.append(f"- {v}")
        md.append("")
        md.append(f"**Narrative:** {card.narrative}\n")

    md.append(f"*Traceability: Chunk `{job['id_A']}` ({job['vA']}) vs Chunk `{job['id_B']}` ({job['vB']})*")
    md.append("\n---\n")
    return md


# ══════════════════════════════════════════════════════════════════════════════
# JOB RUNNER — job-discovery-agnostic, mirrors delta_analyzer.run_delta_jobs()
# ══════════════════════════════════════════════════════════════════════════════

def _as_dict(obj) -> dict:
    """Normalizes a job's profile_A/profile_B/delta field to a plain dict
    regardless of whether it arrived as JSON-loaded dict (Stage 1's own
    run_evolution_analysis(), reading delta_jobs_cache.json off disk) or as
    a live pydantic object straight out of delta_analyzer.run_delta_jobs()
    (Stage 2/fusion_worker.py's in-memory use, which never round-trips
    through JSON) — _build_prompt()/_profile_to_text()/_parse_card() all
    assume dict-style .get()/[] access, same as before this function existed."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


def run_evolution_jobs(jobs: List[Dict]) -> None:
    """
    Builds prompts + calls the LLM for every job in `jobs`, mutating each in
    place with job["card"] (EvolutionCard) on success — left unset on parse
    failure. Job-discovery-agnostic (mirrors delta_analyzer.run_delta_jobs()'s
    reuse contract): callers build `jobs` however they like, as long as each
    job dict has the shape delta_analyzer.run_delta_jobs() already produces
    (topic, vA, vB, profile_A, profile_B, delta, and optionally _profile/
    _fusion_mode). Does not read or write delta_jobs_cache.json/
    evolution_cards_cache.json itself — that I/O stays in
    run_evolution_analysis() below (Stage 1's own caller) or in whatever the
    new caller is (Stage 2's fusion_worker.py, which persists nothing).
    """
    for job in jobs:
        job["profile_A"] = _as_dict(job.get("profile_A"))
        job["profile_B"] = _as_dict(job.get("profile_B"))
        job["delta"]     = _as_dict(job.get("delta"))

    # Per-job profile (job["_profile"]) — not a single "first profile found"
    # guess, so mixed-domain corpora each get their own analyst_role/
    # doc_purpose/dynamic example, not whichever profile happened to load
    # first.
    DEFAULT_ANALYST_ROLE = "a technical analyst"
    DEFAULT_DOC_PURPOSE  = "technical documentation"

    prompts = []
    for job in jobs:
        profile = job.get("_profile") or {}
        analyst_role = profile.get("analyst_role", DEFAULT_ANALYST_ROLE)
        doc_purpose  = profile.get("document_purpose", DEFAULT_DOC_PURPOSE)
        prompts.append(_build_prompt(job, analyst_role, doc_purpose))

    raw_outputs = llm_client.generate_batch(
        prompts, max_tokens=500, desc="Evolution synthesis", stop=["```\n"], enable_thinking=False
    )

    for job, raw in zip(jobs, raw_outputs):
        card = _parse_card(raw, job)
        if card:
            job["card"] = card


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_evolution_analysis():
    log.info("Loading delta jobs cache...")
    if not DELTA_JOBS_CACHE_PATH.exists():
        log.error(f"Delta jobs cache not found: {DELTA_JOBS_CACHE_PATH} — run delta_analyzer.py first.")
        return

    with open(DELTA_JOBS_CACHE_PATH, "r", encoding="utf-8") as f:
        all_jobs = json.load(f)

    jobs = [
        j for j in all_jobs
        if j.get("delta") and _is_constructive(j["delta"].get("change_type", ""))
        and j.get("profile_A") and j.get("profile_B")
    ]
    log.info(f"{len(jobs)}/{len(all_jobs)} jobs are constructive change types — building evolution cards.")
    if not jobs:
        log.warning("No constructive-change jobs found — nothing to do.")
        return

    run_evolution_jobs(jobs)
    kept_jobs = [j for j in jobs if j.get("card")]
    cards = [j["card"] for j in kept_jobs]

    log.info(f"Built {len(cards)} evolution cards.")

    # ── Markdown report ────────────────────────────────────────────────────────
    md: List[str] = []
    global_vA = jobs[0]["vA"] if jobs else "Older"
    global_vB = jobs[0]["vB"] if jobs else "Newer"
    md.append(f"# {global_vA} → {global_vB} — Feature Evolution Report\n")
    md.append(
        "Constructive value-add narratives, synthesized from delta_analyzer.py's "
        "already-extracted profiles and classified deltas. Reversals/contradictions, "
        "no-change/unrelated pairs, deprecations, and purely administrative edits are "
        "intentionally excluded here; see version_delta_report.md for those.\n"
    )
    md.append("## Summary\n")
    md.append("| # | Feature | Change Type |")
    md.append("|---|---------|-------------|")
    for idx, (job, card) in enumerate(zip(kept_jobs, cards), 1):
        md.append(f"| {idx} | **{card.feature_name}** | {card.change_type} |")
    md.append("\n---\n")
    md.append("## Feature Knowledge Cards\n")
    for idx, (job, card) in enumerate(zip(kept_jobs, cards), 1):
        md.extend(_render_card(idx, job, card))

    with open(REPORT_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    log.info(f"✅ Evolution report saved → {REPORT_MD_PATH}")

    # ── Cache for downstream ingestion ────────────────────────────────────────
    cache_out = []
    for job, card in zip(kept_jobs, cards):
        cache_out.append({
            "parent": job["parent"],
            "sub":    job["sub"],
            "topic":  job["topic"],
            "vA":     job["vA"],
            "vB":     job["vB"],
            "id_A":   job["id_A"],
            "id_B":   job["id_B"],
            "card":   card.model_dump(),
        })
    with open(EVOLUTION_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_out, f, indent=2, ensure_ascii=False)
    log.info(f"Evolution cards cache saved → {EVOLUTION_CACHE_PATH}")


if __name__ == "__main__":
    run_evolution_analysis()
