"""
Stage 2/guideline_grounding.py
──────────────────────────────
Patient–Guideline Semantic Enrichment Engine.

Connects the patient knowledge universe (dense per-topic summaries from a
single diagnostic laboratory report) to the guideline knowledge hierarchy
(Stage 1's improved hierarchy summaries), validates guideline applicability
against the patient's ENTIRE knowledge universe, and returns the genuinely
relevant EXISTING guideline topic summaries verbatim — matching is routing,
never rewriting.

The four matching outcomes (§6 of the design brief):
    DIRECT_MATCH       — same underlying clinical subject, guideline adds value
    PARTIAL_MATCH      — related, only a subset of the guideline is relevant
    CONTEXTUAL_MATCH   — relevant only because patient context exists elsewhere
    NO_MATCH           — superficially related, or required context absent

Design rules enforced here (brief §32):
    * UNKNOWN ≠ ABSENT — a missing patient fact is never assumed.
    * NO_MATCH is preferred over a forced match.
    * Only conditions actually supported by the patient universe are surfaced.
    * Patient values are never invented or altered; enrichment is additive.
    * The guideline's curated summary IS the knowledge artifact. The AI decides
      WHICH guideline topics are relevant; it does NOT regenerate their content
      (no extract / fuse / paraphrase — original summaries returned as-is).

Retrieval (§14 / §30):
    * local embedding similarity over the parsed guideline hierarchy topics
      (the improved hierarchy IS the semantic index), merged with
    * cross_corpus_cli.py (existing retrieval infra) results — no vector-DB
      redesign; cross_corpus_cli is reused as a subprocess exactly like
      build_fused_chunks.py does.

Outputs (in <patient output>/guideline_grounded_summaries/):
    <topic_slug>.json   — §22 structured grounding record per patient topic
    <topic_slug>.md     — original patient block verbatim + grounding + original summaries
    grounding_log.json  — §31 per-match explainability log
    _summary_report.md  — per-topic status table
Also patches enterprise_nested_topics.json with an ADDITIVE
`guideline_grounded_summary` / `guideline_grounding` field per topic (original
fields untouched).

Usage:
    cd "Stage 2"
    STAGE2_PATIENT_ID=dr-lalpath-labs python guideline_grounding.py
    STAGE2_PATIENT_ID=dr-lalpath-labs python guideline_grounding.py --limit 3
    STAGE2_PATIENT_ID=dr-lalpath-labs python guideline_grounding.py --no-llm   # retrieval + context only
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

STAGE2_DIR = Path(__file__).parent
sys.path.insert(0, str(STAGE2_DIR))
import stage2_config as s2cfg  # noqa: E402


# ── Canonical clinical-context vocabulary ────────────────────────────────────
# Used BOTH for the deterministic patient context index (regex detection) and
# as the fixed vocabulary the LLM classifier must emit required_context in, so
# validation is deterministic and explainable.
CONTEXT_CONCEPTS: Dict[str, List[str]] = {
    "diabetes": [
        r"\bdiabetes\b", r"\btype\s*1\s*diabetes\b", r"\btype\s*2\s*diabetes\b",
        r"\bt2dm\b", r"\bdiabetic\b", r"\bprediabet\b", r"\bhba1c\b",
        r"\bglycemic\b", r"\bfasting\s+glucose\b", r"\binsulin\b",
    ],
    "hypertension": [
        r"\bhypertension\b", r"\bhypertensive\b", r"\bhtn\b",
        r"\bblood\s+pressure\b", r"\bsystolic\b", r"\bdiastolic\b",
        r"\brace\s+inhibitor\b", r"\barb\b", r"\brenin-angiotensin\b",
    ],
    "ckd": [
        r"\bckd\b", r"\bchronic\s+kidney\b", r"\bkidney\s+failure\b",
        r"\bkidney\s+disease\b", r"\brenal\s+failure\b", r"\bdialysis\b",
        r"\balbuminuria\b", r"\bproteinuria\b", r"\breduced\s+egfr\b",
        r"\begfr\s*<\s*60\b", r"\bmicroalbuminuria\b", r"\bacr\b",
    ],
    "kidney_function_assessment": [
        r"\bcreatinine\b", r"\bkidney\s+function\b", r"\begfr\b", r"\brenal\b",
        r"\bmodified\s+jaffe\b", r"\bkinetic\b", r"\bkidney\s+assessment\b",
    ],
    "elevated_creatinine": [
        r"\belevated\s+creatinine\b", r"\bhigh\s+creatinine\b",
        r"\bcreatinine\s*(?:of\s*)?(1[4-9]|[2-9][0-9])\s*mg/dl\b",
    ],
    "pregnancy": [
        r"\bpregnan", r"\bgestation", r"\blactation\b", r"\bbreastfeed",
        r"\bpostpartum\b", r"\bfetal\b", r"\bconception\b", r"\bchildbearing\b",
        r"\battempting\s+to\s+conceive\b",
    ],
    "ascvd": [
        r"\bascvd\b", r"\batherosclerotic\s+cardiovascular\b",
        r"\bcoronary\s+artery\b", r"\bmyocardial\b", r"\bstroke\b",
        r"\bmace\b", r"\bcvd\b", r"\bcardiovascular\s+disease\b",
        r"\bheart\s+attack\b", r"\batherosclerosis\b",
    ],
    "heart_failure": [
        r"\bheart\s+failure\b", r"\bhfref\b", r"\bhfpef\b", r"\bnt-probnp\b",
        r"\bbnp\b", r"\bcongestive\b",
    ],
    "hypertriglyceridemia": [
        r"\bhypertriglyceridemia\b", r"\btriglyceride\b", r"\bpancreatitis\b",
        r"\bchylomicron\b", r"\belevated\s+tg\b",
    ],
    "familial_hypercholesterolemia": [
        r"\bfamilial\s+hypercholesterolemia\b", r"\bhofh\b", r"\bhefh\b",
        r"\bfh\b", r"\bldlr\b", r"\bgenetic\s+dyslipidemia\b",
    ],
    "obesity": [
        r"\bobesity\b", r"\bobese\b", r"\boverweight\b", r"\bbmi\b",
        r"\badipos", r"\bweight\s+loss\b",
    ],
    "statin_use": [
        r"\bstatin\b", r"\blipid-lowering\s+therapy\b", r"\bllt\b",
        r"\bezetimibe\b", r"\bpcsk9\b", r"\bbempedoic\b", r"\bniacin\b",
    ],
    "dialysis": [
        r"\bdialysis\b", r"\bhemodialysis\b", r"\bperitoneal\s+dialysis\b",
        r"\bkidney\s+transplant\b", r"\brenal\s+replacement\b",
    ],
    "liver_disease": [
        r"\bliver\s+disease\b", r"\bhepatic\b", r"\btransaminase\b",
        r"\bcirrhosis\b", r"\bnafld\b",
    ],
    "thyroid_disease": [
        r"\bthyroid\b", r"\btsh\b", r"\bt3\b", r"\bt4\b", r"\bhypothyroid\b",
        r"\bhyperthyroid\b",
    ],
    "smoking": [r"\bsmok\b", r"\btobacco\b", r"\bcigarette\b", r"\bnicotine\b"],
    "physical_inactivity": [
        r"\bphysical\s+activity\b", r"\bsedentary\b", r"\bexercise\b",
        r"\binactiv\b",
    ],
    "elevated_lp_a": [r"\blp\s*\(a\)\b", r"\blipoprotein\s*\(a\)\b"],
    "elevated_apo_b": [r"\bapob\b", r"\bapo\s+b\b", r"\bapolipoprotein\s+b\b"],
    "elevated_hdl": [
        r"\belevated\s+hdl\b", r"\bhigh\s+hdl\b", r"\bhdl\s+cholesterol.*elevat",
        r"\bhdl\s+.*>\s*200\b",
    ],
    "microalbuminuria": [
        r"\bmicroalbuminuria\b", r"\balbumin-to-creatinine\b", r"\bacr\b",
        r"\burine\s+albumin\b",
    ],
    "cac_imaging": [r"\bcac\b", r"\bcoronary\s+artery\s+calcium\b"],
}

# Key clinical concepts always surfaced in the context index summary.
_CORE_CONTEXT_CONCEPTS = [
    "diabetes", "hypertension", "ckd", "kidney_function_assessment",
    "elevated_creatinine", "pregnancy", "ascvd", "heart_failure",
    "hypertriglyceridemia", "familial_hypercholesterolemia", "obesity",
    "statin_use", "dialysis", "microalbuminuria", "cac_imaging",
]

_NEGATION_WORDS = (
    "no", "not", "without", "never", "negative", "denies", "denied",
    "absence", "absent", "excluded", "rule out", "rules out", "ruled out",
    "no evidence", "no history", "unremarkable",
)


# ── Markdown helpers ─────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80] or "untitled"


def _extract_json_object(text: str) -> Optional[dict]:
    """Robustly extract a single JSON object from LLM output.

    Strips ```json fences and returns the first well-formed {...} via brace
    matching. Falls back to a trailing-comma-tolerant load. If the output is
    truncated mid-JSON (LLM token cap), salvages every COMPLETE numbered entry
    ({"1": {...}, "2": {...}, ...}) so a partial response still classifies the
    candidates the model actually got to.
    """
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return None

    def _match(start_idx: int) -> Optional[dict]:
        """Brace-match the object beginning at start_idx; return parsed dict."""
        depth = 0
        in_str = False
        esc = False
        for i in range(start_idx, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start_idx:i + 1]
                    for attempt in (candidate, candidate.replace(",\n}", "\n}"),
                                    candidate.replace(", }", " }")):
                        try:
                            obj = json.loads(attempt)
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            continue
                    return None
        return None  # unterminated (truncated)

    obj = _match(start)
    if obj is not None:
        return obj

    # Truncation salvage — collect every complete numbered entry.
    salvaged: Dict[str, dict] = {}
    for m in re.finditer(r'"(\d+)"\s*:\s*\{', cleaned):
        entry = _match(m.start() + m.group(0).index("{"))
        if entry:
            salvaged[m.group(1)] = entry
    return salvaged or None


def _parse_patient_topic_file(path: Path) -> Tuple[str, str, str]:
    """Parse a dense per-topic patient summary file.

    Format (single source doc, as written by topic_summarizer.py):
        # <label>
        *Source: <doc>*

        **Patient Result:** ...
        ...
    Returns (label, source_doc, body). The '*Source:' line may span several
    physical lines when the doc name is long.
    """
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


def _parse_bold_sections(body: str) -> Dict[str, str]:
    """Split a topic-summary body into **Section:** → content dict.

    Content is either the rest of the section header line or the following
    indented/bulleted lines up to the next '**...:**' header.
    """
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for ln in body.splitlines():
        m = re.match(r"^\*\*(.+?)\*\*:\s*(.*)$", ln.strip())
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = [m.group(2)] if m.group(2) else []
        elif current is not None:
            buf.append(ln.strip())
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def _split_bullets(text: str) -> List[str]:
    out = []
    for ln in text.splitlines():
        ln = ln.strip().lstrip("-*•").strip()
        if ln:
            out.append(ln)
    return out


# ── Guideline hierarchy parsing ──────────────────────────────────────────────

def _parse_guideline_hierarchy_files(directory: Path) -> Dict[str, dict]:
    """Parse Stage 1's guideline hierarchy summaries into a topic index.

    Returns {label: {parent, sub, topic_summary, source_blocks, ...}}.

    File shape:
        # <Parent>
        ## Sub-Categories / ## Detailed Breakdown
        ## <Sub-category>
        ### <Topic Label>
        <topic-level summary paragraph>
        ### Source 1: <doc>
        **Clinical Recommendation:** ...
        ### Source N: <doc>
        ...
    """
    index: Dict[str, dict] = {}
    if not directory.exists():
        log.warning(f"Guideline hierarchy dir missing: {directory}")
        return index

    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        parent = ""
        for ln in lines:
            if ln.startswith("# "):
                parent = ln[2:].strip()
                break
        current_sub: Optional[str] = None
        current_topic: Optional[dict] = None
        block: Optional[int] = None  # None=summary accumulation, else source idx

        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("## "):
                name = stripped[3:].strip()
                current_sub = None if name in ("Sub-Categories", "Detailed Breakdown") else name
                current_topic = None
                block = None
                continue
            if stripped.startswith("### "):
                name = stripped[4:].strip()
                if re.match(r"^source\s+\d+\s*:", name, re.IGNORECASE):
                    doc = re.split(r":", name, maxsplit=1)[1].strip()
                    if current_topic is None:
                        current_topic = {
                            "label": "(unknown)", "parent": parent, "sub": current_sub,
                            "topic_summary": "", "source_blocks": [],
                        }
                    current_topic["source_blocks"].append({"source_doc": doc, "body": ""})
                    block = len(current_topic["source_blocks"]) - 1
                else:
                    current_topic = {
                        "label": name, "parent": parent, "sub": current_sub,
                        "topic_summary": "", "source_blocks": [],
                    }
                    index.setdefault(name, current_topic)
                    block = None
                continue
            if current_topic is None:
                continue
            if block is None:
                current_topic["topic_summary"] += ln + "\n"
            else:
                current_topic["source_blocks"][block]["body"] += ln + "\n"

    for t in index.values():
        t["topic_summary"] = t["topic_summary"].strip()
        for b in t["source_blocks"]:
            b["body"] = b["body"].strip()
        t["source_docs"] = [b["source_doc"] for b in t["source_blocks"]]
    log.info(f"Parsed {len(index)} guideline topics from {directory}")
    return index


# ── Original guideline summary store ──────────────────────────────────────────
# The guideline per-source topic summaries (Stage 1/data/output/topic_summaries/
# <source_dir>/<label>.md) are the canonical precomputed knowledge artifacts.
# Matching returns the EXISTING summary verbatim — never a rewrite.

_GUIDELINE_SUMMARY_STORE: Optional[Dict[str, List[dict]]] = None


def _load_guideline_summary_store() -> Dict[str, List[dict]]:
    """Scan Stage 1's per-source guideline topic summaries into
    {label: [{source_doc, markdown}]}. Cached per process.

    The markdown is reconstructed in the canonical artifact form:
        # <label>
        *Source: <doc>*
        <body>
    """
    global _GUIDELINE_SUMMARY_STORE
    if _GUIDELINE_SUMMARY_STORE is not None:
        return _GUIDELINE_SUMMARY_STORE
    store: Dict[str, List[dict]] = {}
    topics_dir = s2cfg.GUIDELINES_OUTPUT_DIR / "topic_summaries"
    if not topics_dir.exists():
        log.warning(f"Guideline topic summaries dir missing: {topics_dir}")
        _GUIDELINE_SUMMARY_STORE = store
        return store
    for path in sorted(topics_dir.glob("*/*.md")):
        label, source, body = _parse_patient_topic_file(path)
        if not label:
            continue
        header = f"# {label}\n*Source: {source}*\n\n" if source else f"# {label}\n\n"
        store.setdefault(label, []).append({
            "source_doc": source,
            "markdown": header + body,
        })
    log.info(f"Loaded {sum(len(v) for v in store.values())} original guideline "
             f"summaries for {len(store)} topics from {topics_dir}")
    _GUIDELINE_SUMMARY_STORE = store
    return store


def _original_summary_for(label: str) -> str:
    """Return the ORIGINAL stored summary markdown for a guideline topic label.

    Joins every source-specific copy for the label (multi-source topics like
    'cardiovascular risk assessment' live in several source dirs). Falls back to
    the parsed hierarchy topic summary when the per-source store has no entry.
    """
    store = _load_guideline_summary_store()
    entries = store.get(label)
    if entries:
        return "\n\n---\n\n".join(e["markdown"] for e in entries)
    if _GUIDELINE_INDEX and label in _GUIDELINE_INDEX:
        summary = _GUIDELINE_INDEX[label].get("topic_summary", "")
        if summary:
            return summary
    return ""


def _candidate_score(c: Dict) -> Optional[float]:
    """Best available match score for a candidate (metadata only)."""
    if c.get("rerank_score") is not None:
        return float(c["rerank_score"])
    if c.get("local_score") is not None:
        return float(c["local_score"])
    return None


# ── Patient topic loading ────────────────────────────────────────────────────

_BOILERPLATE_MARKERS = (
    "laboratory information", "validation protocol", "consultant directory",
    "corporate", "office", "address", "customer", "boilerplate",
    "clinical laboratory information", "hematopathology consultant",
    "complete blood count report", "quality filter",
)


def _load_patient_topics(registry_csv: Path, topic_summaries_dir: Path) -> List[dict]:
    """Load the patient's dense per-topic summaries + registry metadata."""
    import pandas as pd

    registry: Dict[str, dict] = {}
    if registry_csv.exists():
        df = pd.read_csv(registry_csv)
        for _, row in df.iterrows():
            label = row.get("master_label", "")
            if label:
                registry[label] = row.to_dict()

    topics: List[dict] = []
    if topic_summaries_dir.exists():
        for path in sorted(topic_summaries_dir.glob("*/*.md")):
            label, source, body = _parse_patient_topic_file(path)
            if not label:
                continue
            sections = _parse_bold_sections(body)
            reg = registry.get(label, {})
            topics.append({
                "master_label": label,
                "source_docs": [source] if source else [str(reg.get("source_docs", ""))],
                "file": str(path),
                "body": body,
                "result": sections.get("Patient Result", ""),
                "interpretation": sections.get("Clinical Interpretation", ""),
                "significance": sections.get("Significance Level", ""),
                "recommendation": sections.get("Clinical Recommendation", ""),
                "actions": _split_bullets(sections.get("Key Clinical Actions", "")),
                "preparation": _split_bullets(sections.get("Patient Preparation / Diagnostic Criteria", "")),
                "description": str(reg.get("description", "") or ""),
                "keywords": str(reg.get("keywords", "") or ""),
                "summarized_description": str(reg.get("summarized_description", "") or ""),
                "grounded_summary": str(reg.get("grounded_summary", "") or ""),
                "qna": reg.get("qna", ""),
                "is_boilerplate": any(m in label.lower() for m in _BOILERPLATE_MARKERS),
            })

    # Fallback: if no dense topic summaries exist, build from the registry.
    if not topics and registry:
        for label, reg in registry.items():
            topics.append({
                "master_label": label,
                "source_docs": [str(reg.get("source_docs", ""))],
                "file": "",
                "body": str(reg.get("summarized_description", "") or ""),
                "result": "", "interpretation": "", "significance": "",
                "recommendation": "",
                "actions": [], "preparation": [],
                "description": str(reg.get("description", "") or ""),
                "keywords": str(reg.get("keywords", "") or ""),
                "summarized_description": str(reg.get("summarized_description", "") or ""),
                "grounded_summary": str(reg.get("grounded_summary", "") or ""),
                "qna": reg.get("qna", ""),
                "is_boilerplate": any(m in label.lower() for m in _BOILERPLATE_MARKERS),
            })
    log.info(f"Loaded {len(topics)} patient topics")
    return topics


def _patient_topic_text(topic: dict) -> str:
    """The patient topic's own semantic content (used as matching query)."""
    parts = [topic["master_label"], topic["body"], topic["description"]]
    if topic["keywords"]:
        parts.append("Keywords: " + topic["keywords"])
    text = "\n".join(p for p in parts if p and p.strip())
    return text.strip()


def _patient_universe_text(topics: List[dict]) -> str:
    """All patient topic content + registry hints — the complete knowledge universe."""
    chunks = []
    for t in topics:
        chunks.append(t.get("body") or "")
        if t.get("description"):
            chunks.append(t["description"])
        if t.get("keywords"):
            chunks.append(t["keywords"])
    return "\n\n".join(c for c in chunks if c and c.strip())


# ── Patient context index ────────────────────────────────────────────────────

def _is_negated(text: str, match_start: int) -> bool:
    window = text[max(0, match_start - 60):match_start].lower()
    return any(w in window for w in _NEGATION_WORDS)


def _concept_state(text: str, patterns: List[str]) -> str:
    seen_plain = False
    seen_negated = False
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if _is_negated(text, m.start()):
                seen_negated = True
            else:
                seen_plain = True
    if seen_plain:
        return "PRESENT"
    if seen_negated:
        return "ABSENT"
    return "UNKNOWN"


def build_patient_context_index(topics: List[dict]) -> Dict[str, str]:
    """Scan the whole patient knowledge universe → {concept: PRESENT|ABSENT|UNKNOWN}."""
    universe = _patient_universe_text(topics)
    index: Dict[str, str] = {}
    for concept, patterns in CONTEXT_CONCEPTS.items():
        index[concept] = _concept_state(universe, patterns)
    return index


def render_context_index_summary(index: Dict[str, str], concepts: Optional[List[str]] = None) -> str:
    names = concepts or _CORE_CONTEXT_CONCEPTS
    lines = []
    for c in names:
        state = index.get(c, "UNKNOWN")
        lines.append(f"{c} = {state}")
    return "; ".join(lines)


# ── Matching query (brief §13) ───────────────────────────────────────────────

def _build_matching_query(topic: dict) -> str:
    """Compact semantic representation for retrieval — avoids over-weighting
    report boilerplate / office metadata / the report's own printed
    recommendations. The clinical concept matters, not the printed advice."""
    if topic["is_boilerplate"]:
        # Boilerplate-named topics often still carry real clinical content in
        # their recommendation/action/preparation sections (e.g. a "Clinical
        # Laboratory Information" topic that actually contains lipid-testing
        # guidance) — include those so retrieval isn't dominated by the
        # administrative-sounding label. Report print metadata (result /
        # interpretation) is still excluded.
        return " ".join(
            p for p in [topic["master_label"], topic["description"], topic["keywords"],
                        topic["recommendation"],
                        " ".join(topic.get("actions") or []),
                        " ".join(topic.get("preparation") or [])]
            if p and p.strip()
        ).strip()[:1200]
    parts = [
        topic["master_label"],
        topic["description"],
        topic["result"],
        topic["interpretation"],
        topic["keywords"],
    ]
    query = " ".join(p for p in parts if p and p.strip())
    return query.strip()[:1200] or topic["master_label"]


# ── Retrieval: local hierarchy-index similarity + cross_corpus ──────────────

_EMBEDDER = None
_GUIDELINE_INDEX = None


def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer
        _EMBEDDER = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2", device="cpu")
    return _EMBEDDER


def _get_guideline_index(guideline_hierarchy_dir: Path) -> Dict[str, dict]:
    global _GUIDELINE_INDEX
    if _GUIDELINE_INDEX is None:
        _GUIDELINE_INDEX = _parse_guideline_hierarchy_files(guideline_hierarchy_dir)
    return _GUIDELINE_INDEX


def _guideline_embed_text(topic: dict) -> str:
    parts = [topic.get("label") or topic.get("master_label", ""), topic.get("topic_summary", "")]
    if topic.get("sub"):
        parts.append(topic["sub"])
    if topic.get("parent"):
        parts.append(topic["parent"])
    return " ".join(p for p in parts if p and p.strip())


def _local_topic_retrieval(guideline_index: Dict[str, dict], query: str, k: int) -> List[Dict]:
    """Embedding similarity over the parsed guideline hierarchy topics."""
    import numpy as np

    embedder = _get_embedder()
    keys = list(guideline_index.keys())
    cache_path = s2cfg.GUIDELINES_OUTPUT_DIR / "guideline_grounding_cache" / "topic_embeddings.npy"
    keys_path = s2cfg.GUIDELINES_OUTPUT_DIR / "guideline_grounding_cache" / "topic_keys.json"
    if cache_path.exists() and keys_path.exists():
        emb = np.load(cache_path)
        cached_keys = json.loads(keys_path.read_text(encoding="utf-8"))
        if cached_keys == keys and emb.shape[0] == len(keys):
            pass
        else:
            emb = None
    else:
        emb = None
    if emb is None:
        log.info(f"Building guideline topic embeddings for {len(keys)} topics...")
        texts = [_guideline_embed_text(guideline_index[k]) for k in keys]
        emb = embedder.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, emb)
        keys_path.write_text(json.dumps(keys), encoding="utf-8")

    q = embedder.encode(query, normalize_embeddings=True, convert_to_numpy=True)
    scores = emb @ q
    top = np.argsort(scores)[::-1][:k]
    out = []
    for idx in top.tolist():
        label = keys[idx]
        t = guideline_index[label]
        out.append({
            "master_label": label,
            "parent": t.get("parent", ""),
            "sub": t.get("sub", ""),
            "topic_summary": t.get("topic_summary", ""),
            "source_blocks": t.get("source_blocks", []),
            "source_docs": t.get("source_docs", []),
            "local_score": round(float(scores[idx]), 4),
            "rerank_score": None,
            "chunk_id": None,
            "chunk_text": None,
            "grounded_summary": None,
            "summarized_description": None,
        })
    return out


def _run_cross_corpus_batch(queries: List[Dict], k: int) -> Dict[str, List[Dict]]:
    """Shell out to retrieval_layer/cross_corpus_cli.py (existing infra)."""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "queries.json"
        out_path = Path(tmp) / "results.json"
        in_path.write_text(json.dumps(queries), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(s2cfg.CROSS_CORPUS_CLI_PATH),
             "--in", str(in_path), "--out", str(out_path), "--k", str(k)],
            cwd=str(s2cfg.CROSS_CORPUS_CLI_PATH.parent),
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            log.error(f"cross_corpus_cli.py failed:\n{result.stderr[-1500:]}")
            return {}
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        return payload.get("results", {})


# Administrative/non-clinical guideline topics must NEVER enrich a patient topic
# — author teams, funding, COI, governance, contact info, journal metadata, etc.
# are about the guideline itself, not the patient's clinical subject (§32:
# NO_MATCH preferred over a forced match; only genuinely relevant guideline info).
_GUIDELINE_ADMIN_MARKERS = (
    "author team", "committee", "writing committee", "funding", "grant",
    "conflict of interest", "disclosure", "governance", "approval process",
    "development process", "development policy", "development contributors",
    "peer review", "contact information", "institutional", "journal metadata",
    "trial enrollment", "task force member", "topic selection",
    "update prioritization", "iteration update", "methodology standards",
    "evidence-based guideline development", "scope and limitations",
    "affiliations", "transparency",
)


def _is_admin_guideline_topic(label: str) -> bool:
    low = label.lower()
    return any(m in low for m in _GUIDELINE_ADMIN_MARKERS)


# Administrative patient topics (directories, contact/office metadata) carry no
# clinical subject of their own and must not be clinically enriched.
_PATIENT_ADMIN_MARKERS = ("directory", "contact information", "office address",
                          "customer service", "credentials", "affiliations")


def _is_admin_patient_topic(topic: dict) -> bool:
    low = " ".join([
        topic.get("master_label", ""),
        topic.get("description", ""),
        topic.get("body", ""),
    ]).lower()
    return any(m in low for m in _PATIENT_ADMIN_MARKERS)


def _merge_candidates(local: List[Dict], xcorp: List[Dict]) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    for c in local:
        if _is_admin_guideline_topic(c["master_label"]):
            continue
        merged[c["master_label"]] = dict(c)
    for c in xcorp:
        label = c.get("master_label", "")
        if not label or _is_admin_guideline_topic(label):
            continue
        if label in merged:
            # Prefer local index's source blocks; keep cross_corpus score.
            merged[label]["rerank_score"] = c.get("score")
            if not merged[label]["topic_summary"]:
                merged[label]["topic_summary"] = (
                    c.get("grounded_summary") or c.get("summarized_description") or "")
            merged[label]["grounded_summary"] = c.get("grounded_summary")
            merged[label]["summarized_description"] = c.get("summarized_description")
            merged[label]["chunk_id"] = c.get("chunk_id")
            merged[label]["chunk_text"] = c.get("chunk_text")
            if not merged[label]["source_docs"]:
                merged[label]["source_docs"] = [str(c.get("source_docs", ""))]
        else:
            merged[label] = {
                "master_label": label,
                "parent": "", "sub": "",
                "topic_summary": c.get("grounded_summary") or c.get("summarized_description") or "",
                "source_blocks": [],
                "source_docs": [str(c.get("source_docs", ""))] if c.get("source_docs") else [],
                "local_score": None,
                "rerank_score": c.get("score"),
                "chunk_id": c.get("chunk_id"),
                "chunk_text": c.get("chunk_text"),
                "grounded_summary": c.get("grounded_summary"),
                "summarized_description": c.get("summarized_description"),
            }
    scored = sorted(
        merged.values(),
        key=lambda c: (c["rerank_score"] if c["rerank_score"] is not None
                       else c["local_score"] if c["local_score"] is not None else -99.0),
        reverse=True,
    )
    return scored


def retrieve_guideline_topic_candidates(
    patient_topic: dict,
    guideline_index: Dict[str, dict],
    local_k: int = 20,
    xcorp_k: int = 10,
    xcorp_by_topic: Optional[Dict[str, List[Dict]]] = None,
) -> List[Dict]:
    query = _build_matching_query(patient_topic)
    local = _local_topic_retrieval(guideline_index, query, k=local_k)
    xcorp = xcorp_by_topic.get(patient_topic["master_label"], []) if xcorp_by_topic else []
    return _merge_candidates(local, xcorp)


# ── LLM classification (brief §16 / §17 / §2) ────────────────────────────────

_CLASSIFY_SYSTEM = (
    "You are a clinical knowledge-bridge analyst. You decide whether a "
    "guideline topic genuinely enriches a patient topic from a diagnostic "
    "laboratory report. Being conservative and correct is more important than "
    "finding a match."
)

_CLASSIFY_INSTRUCTIONS = """\
Decide, for every candidate, whether the guideline topic enriches the PATIENT TOPIC.

CRITICAL DISTINCTION — "same domain" is NOT "same subject":
  - "Lipid panel measurement" vs "Lipid measurement guideline update"  → same subject (DIRECT).
  - "Lipid panel measurement" vs "Lipid treatment thresholds"          → same DOMAIN only, different subject (NO_MATCH).
  - "Kidney function assessment" vs "kidney management in diabetes"    → same subject, but needs diabetes context (CONTEXTUAL if present).
Do not treat a shared keyword (lipid / kidney / diabetes / cholesterol) as a match by itself.

PATIENT CONTEXT DECIDES DOMAIN MATCHES:
  When the PATIENT KNOWLEDGE UNIVERSE marks a clinical concept the guideline
  targets as PRESENT (e.g. ascvd, statin_use, hypertriglyceridemia), and the
  patient topic is a decision framework / management / treatment topic in that
  same domain, then the guideline DOES enrich the topic — classify DIRECT_MATCH
  (or CONTEXTUAL_MATCH if it additionally requires a specific patient state that
  is UNKNOWN). A brief patient topic body is NOT a reason for NO_MATCH when the
  patient's confirmed context establishes the domain. Do not invent conditions;
  UNKNOWN/ABSENT context never creates a match.

Match types:
  - DIRECT_MATCH:      essentially the same underlying clinical subject AND the guideline adds directly useful information.
  - PARTIAL_MATCH:     related, but only a subset of the guideline is relevant to this patient topic (do NOT attach the whole guideline).
  - CONTEXTUAL_MATCH:  becomes relevant only because patient context exists somewhere in the patient's knowledge universe (see PATIENT CONTEXT below).
  - NO_MATCH:          only superficially related, or required patient context is absent/unknown, or unsure.

Rules:
  - NEVER force a match. When unsure → NO_MATCH.
  - Do not invent patient conditions. Only use the PATIENT CONTEXT states given.
  - required_context: only from the allowed concept list, only concepts whose presence the guideline actually REQUIRES to be applicable to this patient.
  - additional_context: useful-but-not-required concepts (never implied as present if UNKNOWN/ABSENT).
  - relevant_portions: 1-3 short phrases naming which part of the guideline is relevant.

Allowed context concepts:
{concepts}

PATIENT TOPIC:
Label: {label}
{patient_body}

PATIENT KNOWLEDGE UNIVERSE (whole report):
{context_summary}

CANDIDATE GUIDELINE TOPICS:
{candidates}

Output ONLY a single JSON object — nothing before or after it — mapping each candidate number to its classification:
{{
  "1": {{
    "status": "DIRECT_MATCH|PARTIAL_MATCH|CONTEXTUAL_MATCH|NO_MATCH",
    "confidence": 0.0,
    "match_reason": "short reason",
    "required_context": [],
    "additional_context": [],
    "relevant_portions": []
  }},
  "2": {{ ... }}
}}
Complete all closing braces. Never leave JSON unfinished."""


def _build_classify_prompt(topic: dict, candidates: List[Dict], context_summary: str) -> str:
    concepts = ", ".join(_CORE_CONTEXT_CONCEPTS)
    cand_lines = []
    for i, c in enumerate(candidates, start=1):
        domain = " > ".join(p for p in [c["parent"], c["sub"]] if p)
        summary = c["topic_summary"][:600] or c["summarized_description"] or c["grounded_summary"] or ""
        cand_lines.append(f"{i}. {c['master_label']}" + (f"  (domain: {domain})" if domain else ""))
        if summary:
            cand_lines.append(f"   {summary[:500]}")
    body = topic["body"] or topic["summarized_description"] or topic["description"]
    return _CLASSIFY_INSTRUCTIONS.format(
        concepts=concepts,
        label=topic["master_label"],
        patient_body=(body or "")[:2000],
        context_summary=context_summary[:1500],
        candidates="\n".join(cand_lines),
    )


def _parse_classify_output(raw: str, candidates: List[Dict]) -> Dict[str, dict]:
    obj = _extract_json_object(raw) or {}
    out: Dict[str, dict] = {}
    for i, cand in enumerate(candidates, start=1):
        key = str(i)
        entry = obj.get(key)
        if not isinstance(entry, dict):
            entry = obj.get(cand["master_label"])
        if not isinstance(entry, dict):
            out[cand["master_label"]] = {
                "status": "NO_MATCH",
                "confidence": 0.0,
                "match_reason": "classifier returned no entry for this candidate",
                "required_context": [], "additional_context": [],
                "relevant_portions": [], "new_information_summary": "",
            }
            continue
        status = str(entry.get("status", "NO_MATCH")).upper()
        if status not in ("DIRECT_MATCH", "PARTIAL_MATCH", "CONTEXTUAL_MATCH", "NO_MATCH"):
            status = "NO_MATCH"
        try:
            conf = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        req = entry.get("required_context") or []
        if not isinstance(req, list):
            req = [req]
        add = entry.get("additional_context") or []
        if not isinstance(add, list):
            add = [add]
        rel = entry.get("relevant_portions") or []
        if not isinstance(rel, list):
            rel = [rel]
        out[cand["master_label"]] = {
            "status": status,
            "confidence": round(min(max(conf, 0.0), 1.0), 3),
            "match_reason": str(entry.get("match_reason", "") or ""),
            "required_context": [str(c).strip() for c in req if str(c).strip()],
            "additional_context": [str(c).strip() for c in add if str(c).strip()],
            "relevant_portions": [str(r).strip() for r in rel if str(r).strip()],
            "new_information_summary": str(entry.get("new_information_summary", "") or ""),
        }
    return out


# ── Fuse patient topic + matched guideline knowledge (brief §21 / §22) ───────
# The AI decides WHICH guideline topics are relevant (classification). This
# step weaves the matched ORIGINAL guideline summaries into the patient's own
# topic summary — patient-first, additive, attributed. It does NOT regenerate
# the guideline summaries; it references the curated knowledge already stored.

_FUSE_SYSTEM = (
    "You are a clinical summary writer. You enrich a patient's own topic "
    "summary with relevant guideline knowledge from matched guideline "
    "summaries. You never change the patient's facts, values, or wording, and "
    "you never invent guideline content — you only add, with attribution, what "
    "the matched guideline summaries actually say."
)

_FUSE_INSTRUCTIONS = """\
Produce the PATIENT GROUNDED SUMMARY: the patient's original topic summary,
enriched with the applicable knowledge from the MATCHED GUIDELINE SUMMARIES.

Rules:
1. PATIENT FIRST — start from the patient's own sections and wording. Never
   rewrite the report as if the guideline generated the finding.
2. Do not change, drop, or invent any patient values, results, or facts.
3. Keep the patient's own section structure and headings exactly
   (Patient Result / Clinical Interpretation / Significance Level /
   Clinical Recommendation / Key Clinical Actions /
   Patient Preparation / Diagnostic Criteria).
4. Add guideline knowledge ONLY where it is genuinely applicable to THIS
   patient topic, phrased as attributed context
   ("Clinical guidelines suggest ...", "guideline context / considerations /
   applicable guidance: ..."), never as a prescription for this patient.
5. GUIDED COMPLETENESS: weave in the guideline knowledge that genuinely
   applies to THIS patient topic — include the applicable recommendations,
   thresholds, and decision criteria from the matched guideline summaries
   (typically 3-8 guideline bullets per section where applicable, more when the
   guideline provides detailed actionable guidance). Each is a single bullet,
   phrased as attributed context ("Clinical guidelines suggest ...",
   "guideline context / considerations / applicable guidance: ..."), never as
   a prescription for this patient. Attribute the guideline source (its name
   and year) when the MATCHED GUIDELINE SUMMARIES provide it.
6. DEDUPLICATE: if two or more guideline points state the same idea (for
   example, several guidelines all say "individualize glycemic targets based
   on patient factors"), combine them into ONE bullet that captures the
   common point. Never restate a point the patient's own summary already
   makes, and never emit the same idea twice in different words. A point that
   appears in the patient's own bullets should NOT be repeated as guideline
   context.
7. If a guideline point only applies conditionally, say so explicitly.
8. PATIENT-FIRST LENGTH: the enriched summary must retain the patient's own
   sections verbatim and may exceed the original patient topic in length —
   it is the patient's report enriched with the applicable guideline
   knowledge, not a stripped-down or condensed rewrite.

PATIENT TOPIC (preserve verbatim):
Label: {label}
{patient_body}

MATCHED GUIDELINE SUMMARIES (original curated summaries — reference, do not regenerate):
{guideline_block}

Output ONLY the enriched patient grounded summary as plain markdown text —
the same section structure and headings as the PATIENT TOPIC. Do not add
headers, explanations, or commentary outside the summary."""


def _build_fuse_prompt(topic: dict, matched: List[dict]) -> str:
    body = topic["body"] or topic["summarized_description"] or topic["description"]
    parts = []
    for m in matched:
        summary = m.get("summary") or ""
        if not summary:
            continue
        src = ", ".join(m.get("source_docs", []))
        parts.append(f"[{m['master_label']}]"
                     + (f" (source: {src})" if src else "") + "\n" + summary)
    return _FUSE_INSTRUCTIONS.format(
        label=topic["master_label"],
        patient_body=(body or "")[:4000],
        guideline_block="\n\n---\n\n".join(parts)[:12000] or "(no applicable guideline summary available)",
    )


# Free OpenRouter models (e.g. gemma-4-26b:free) hard-cap output well below
# max_tokens, silently cutting long fused summaries mid-word. Detect that and
# re-run the affected topic through Groq (llama-3.3-70b, 32k output cap).
def _is_truncated(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    # A mid-sentence/mid-word cut is the signature of a provider output cap.
    return text[-1] not in ".!?)]}\"\'*"


def _retry_fusion_groq(prompt: str, fallback: str, system_prompt: Optional[str]) -> str:
    import llm_client as _llm_client
    groq_client = _llm_client._get_groq_client()
    if groq_client is None:
        log.warning("GROQ_API_KEY not set — keeping truncated fusion output")
        return ""
    try:
        out = _llm_client._chat_openai_compatible(
            groq_client, _llm_client.config.GROQ_MODEL, prompt, 4000, 0.0,
            system_prompt, None, False, label="Groq",
        )
        out = (out or "").strip()
        if _is_truncated(out):
            log.warning(f"Groq fusion retry still truncated ({len(out)} chars) — keeping first pass")
            return ""
        return out
    except Exception as e:  # noqa: BLE001
        log.warning(f"Groq fusion retry failed ({type(e).__name__}: {e}) — keeping first pass")
        return ""


# ── Deterministic dedup of fused bullets ──────────────────────────────────────
# LLMs reliably produce redundant bullets (multiple "Guideline context:" lines
# restating the same idea — e.g. "individualize glycemic targets" appears 4x).
# Relying on a prompt rule is unreliable; instead we drop near-duplicate bullets
# per section using embedding cosine similarity. Patient-original bullets always
# come first in the fused output, so "first occurrence wins" keeps them and only
# strips the duplicated guideline echoes that follow.

_SECTION_HEADING_RE = re.compile(r"^\*\*(.+?):\*\*\s*$|^#{1,3}\s+.+$")
_DEDUP_SIM_THRESHOLD = 0.75
# Only guideline-attributed bullets are candidates for removal — a patient's own
# clinical facts are never dropped, even when a later guideline bullet echoes them.
_GUIDELINE_BULLET_RE = re.compile(
    r"(guideline|clinical guidelines|applicable guidance|guidance|consider\b)",
    re.IGNORECASE,
)


def _dedupe_fused_bullets(fused: str) -> str:
    """Collapse near-identical bullet lines within each section of a fused summary."""
    lines = (fused or "").splitlines()
    if not lines:
        return fused or ""
    _embedder = None

    def _embed(texts: List[str]):
        nonlocal _embedder
        if _embedder is None:
            from ingest import _get_embedder
            _embedder = _get_embedder()
        return _embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def _section_lines(sec: List[str]) -> List[str]:
        return [ln for ln in sec if ln.strip().startswith("- ")]

    def _dedup_section(sec: List[str]) -> List[str]:
        bullets = _section_lines(sec)
        if len(bullets) < 2:
            return sec
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np  # noqa: F401
        embs = _embed(bullets)
        sim = cosine_similarity(embs)
        keep = [True] * len(bullets)
        for i in range(len(bullets)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(bullets)):
                if keep[j] and sim[i][j] >= _DEDUP_SIM_THRESHOLD:
                    # Never drop a patient-original fact bullet; only the
                    # guideline echo that restates it. If BOTH bullets are
                    # guideline-attributed, keep the first (patient bullets
                    # always precede guideline additions in a section).
                    if _GUIDELINE_BULLET_RE.search(bullets[j]):
                        keep[j] = False
        # Rebuild section: skip dropped bullets, keep everything else in order.
        out, bi = [], 0
        for ln in sec:
            if ln.strip().startswith("- "):
                if keep[bi]:
                    out.append(ln)
                bi += 1
            else:
                out.append(ln)
        return out

    sections, cur = [], []
    for ln in lines:
        if _SECTION_HEADING_RE.match(ln.strip()) and cur:
            sections.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        sections.append(cur)

    deduped = [_dedup_section(s) for s in sections]
    return "\n".join("\n".join(s) for s in deduped)


# ── Deterministic patient-context validation (brief §5 / §19 / §32) ──────────

def validate_patient_context(classification: Dict[str, dict], context_index: Dict[str, str]) -> Dict[str, dict]:
    """Check every non-NO_MATCH candidate's required_context against the index.

    A required concept that is ABSENT or UNKNOWN in the patient universe
    downgrades the candidate to NO_MATCH (conservative, §32). The concepts
    that are PRESENT become validated_context; the rest rejected_context.
    """
    for label, entry in classification.items():
        if entry["status"] == "NO_MATCH":
            entry["validated_context"] = []
            entry["rejected_context"] = []
            continue
        required = entry.get("required_context", [])
        validated, rejected = [], []
        for concept in required:
            state = context_index.get(concept, "UNKNOWN")
            if state == "PRESENT":
                validated.append(concept)
            else:
                rejected.append((concept, state))
        entry["validated_context"] = validated
        entry["rejected_context"] = rejected
        if rejected:
            reason = entry["match_reason"] or "candidate requires patient context"
            entry["match_reason"] = (
                f"{reason} | REJECTED: required patient context not established "
                f"in the patient knowledge universe: "
                + ", ".join(f"{c}={s}" for c, s in rejected)
            )
            entry["status"] = "NO_MATCH"
            entry["confidence"] = 0.0
    return classification


# ── Orchestration ────────────────────────────────────────────────────────────

def _final_status(classification: dict) -> str:
    statuses = [c["status"] for c in classification.values()]
    if not statuses:
        return "NO_MATCH"
    if any(s == "DIRECT_MATCH" for s in statuses):
        return "DIRECT_MATCH"
    if any(s == "PARTIAL_MATCH" for s in statuses):
        return "PARTIAL_MATCH"
    if any(s == "CONTEXTUAL_MATCH" for s in statuses):
        return "CONTEXTUAL_MATCH"
    return "NO_MATCH"


def _write_outputs(
    output_dir: Path,
    topics: List[dict],
    results: Dict[str, dict],
    log_entries: List[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    report_lines = ["# Guideline Grounding Report", "",
                    "| Patient Topic | Status | Matched guideline topics |", "|---|---|---|"]
    for topic in topics:
        slug = _slugify(topic["master_label"])
        res = results[topic["master_label"]]
        status = res["grounding"]["status"]
        matched = res["grounding"]["matched_guideline_topics"]
        matched_str = ", ".join(m["master_label"] for m in matched) or "—"
        report_lines.append(f"| {topic['master_label']} | {status} | {matched_str} |")

        (output_dir / f"{slug}.json").write_text(
            json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")

        md = [
            f"# {topic['master_label']}", "",
            "## Original Patient Summary", "",
            topic["body"] or topic["summarized_description"] or "", "",
            "## Grounding", "",
            f"**Status:** {status}", "",
        ]
        if matched:
            md.append("**Matched guideline topics:**")
            for m in matched:
                md.append(f"- **{m['master_label']}** — {m['match_reason']}")
                if m.get("validated_context"):
                    md.append(f"  - validated context: {', '.join(m['validated_context'])}")
                if m.get("rejected_context"):
                    md.append(f"  - rejected context: {', '.join(m['rejected_context'])}")
                if m.get("score") is not None:
                    md.append(f"  - match score: {m['score']:.4f}")
                if m.get("source_docs"):
                    md.append(f"  - source: {', '.join(m['source_docs'])}")
                if m.get("summary"):
                    md.append("  - original guideline summary:")
                    md.append("")
                    for ln in m["summary"].splitlines():
                        md.append(f"    {ln}")
                    md.append("")
            md.append("")
        else:
            md.append("No matching guideline topics passed context validation.")
            md.append("")
        md += ["## Fused Summary", "",
               res.get("fused_summary") or res["grounded_summary"], ""]
        if res.get("fuse_source_labels"):
            md += ["**Fused from:** " + ", ".join(res["fuse_source_labels"]), ""]
        md += ["## Provenance", "",
               f"- Patient sources: {', '.join(topic['source_docs'])}",
               f"- Guideline sources: {', '.join(res['provenance']['guideline_sources']) or '—'}", ""]
        (output_dir / f"{slug}.md").write_text("\n".join(md), encoding="utf-8")

        # Overwrite the patient's own per-topic summary file with the fused
        # summary, preserving the '# <label>' + '*Source: <doc>*' header so
        # downstream parsers (topic_summarizer/hierarchy_topic_merge) keep working.
        tfile = Path(topic["file"]) if topic.get("file") else None
        if tfile and tfile.exists():
            source_doc = topic["source_docs"][0] if topic.get("source_docs") else ""
            fused = res.get("fused_summary") or res["grounded_summary"]
            tfile.write_text(
                f"# {topic['master_label']}\n*Source: {source_doc}*\n\n{fused}\n",
                encoding="utf-8",
            )

    (output_dir / "_summary_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    (output_dir / "grounding_log.json").write_text(
        json.dumps(log_entries, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Wrote {len(topics)} grounded topics → {output_dir}")


def _patch_nested_json(nested_path: Path, results: Dict[str, dict]) -> int:
    if not nested_path.exists():
        log.warning(f"Nested taxonomy JSON missing: {nested_path} — skipping additive patch")
        return 0
    with open(nested_path, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for parent in data.get("taxonomy", []):
        for sub in parent.get("sub_categories", []):
            for topic in sub.get("topics", []):
                label = topic.get("master_label", "")
                res = results.get(label)
                if not res:
                    continue
                topic["guideline_grounded_summary"] = res["grounded_summary"]
                topic["guideline_grounded_summary_fused"] = res.get("fused_summary", res["grounded_summary"])
                topic["guideline_grounding"] = {
                    "status": res["grounding"]["status"],
                    "matched_guideline_topics": [
                        {
                            "master_label": m["master_label"],
                            "match_type": m["match_type"],
                            "confidence": m["confidence"],
                            "score": m.get("score"),
                            "match_reason": m["match_reason"],
                            "validated_context": m.get("validated_context", []),
                            "rejected_context": m.get("rejected_context", []),
                            "source_docs": m.get("source_docs", []),
                            "summary": m.get("summary", ""),
                        }
                        for m in res["grounding"]["matched_guideline_topics"]
                    ],
                }
                n += 1
    with open(nested_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return n


def run_guideline_grounding(patient_id: str, limit: int = 0, no_llm: bool = False) -> Dict[str, dict]:
    """Main enrichment pipeline. Returns {topic_label: §22 record}."""
    s2cfg.setup_patient_env(patient_id)
    s2cfg.put_stage1_first_on_path()

    import config  # Stage 1's config.py, env-pointed at the patient corpus

    topic_summaries_dir = config.OUTPUT_DIR / "topic_summaries"
    registry_csv = config.REGISTRY_PATH
    nested_path = config.NESTED_OUTPUT_PATH
    output_dir = config.OUTPUT_DIR / "guideline_grounded_summaries"
    guideline_hierarchy_dir = s2cfg.GUIDELINES_OUTPUT_DIR / "hierarchy_summaries"

    topics = _load_patient_topics(registry_csv, topic_summaries_dir)
    if limit and limit > 0:
        topics = topics[:limit]
    if not topics:
        log.error("No patient topics found — run Stage 2 pipeline first")
        return {}

    # 1. Patient-wide context index
    context_index = build_patient_context_index(topics)
    log.info(f"Patient context index: { {k: context_index[k] for k in _CORE_CONTEXT_CONCEPTS} }")

    # 2. Guideline hierarchy index
    guideline_index = _get_guideline_index(guideline_hierarchy_dir)
    if not guideline_index:
        log.error(f"No guideline topics parsed from {guideline_hierarchy_dir}")
        return {}

    # 3. Retrieve candidates (local similarity always; cross_corpus batch once)
    queries = [{"id": t["master_label"], "query": _build_matching_query(t)} for t in topics]
    xcorp_by_topic: Dict[str, List[Dict]] = {}
    if not no_llm:
        log.info(f"Cross-corpus guideline lookup for {len(queries)} patient topics (k=10)...")
        xcorp_by_topic = _run_cross_corpus_batch(queries, 10)
        log.info(f"Cross-corpus results for {len(xcorp_by_topic)} topics")

    candidates_by_topic: Dict[str, List[Dict]] = {}
    for t in topics:
        candidates_by_topic[t["master_label"]] = retrieve_guideline_topic_candidates(
            t, guideline_index, xcorp_by_topic=xcorp_by_topic)

    results: Dict[str, dict] = {}
    log_entries: List[dict] = []

    if no_llm:
        log.info("--no-llm: skipping LLM classification (retrieval only)")
        _load_guideline_summary_store()
        for t in topics:
            matched = []
            for c in candidates_by_topic[t["master_label"]][:5]:
                summary = _original_summary_for(c["master_label"])
                matched.append({
                    "master_label": c["master_label"],
                    "match_type": "CONTEXTUAL_MATCH",  # retrieval-only; classifier skipped
                    "confidence": 0.0,
                    "score": _candidate_score(c),
                    "match_reason": "LLM classification disabled (--no-llm); retrieval candidate only",
                    "context_requirements": [],
                    "validated_context": [],
                    "rejected_context": [],
                    "source_docs": c.get("source_docs", []),
                    "summary": summary,
                })
            results[t["master_label"]] = {
                "master_label": t["master_label"],
                "original_summary": t["body"],
                "grounding": {
                    "status": "CONTEXTUAL_MATCH" if matched else "NO_MATCH",
                    "matched_guideline_topics": matched,
                },
                "fused_summary": t["body"],  # retrieval-only — nothing to fuse
                "fuse_source_labels": [m["master_label"] for m in matched],
                "grounded_summary": t["body"],
                "provenance": {"patient_sources": t["source_docs"],
                               "guideline_sources": sorted({d for m in matched for d in m["source_docs"]})},
            }
        _write_outputs(output_dir, topics, results, log_entries)
        return results

    # 4. Classify relationships (batched per patient topic)
    import llm_client
    class_prompts = [
        _build_classify_prompt(t, candidates_by_topic[t["master_label"]],
                               render_context_index_summary(context_index))
        for t in topics
    ]
    log.info(f"Classifying topic relationships for {len(class_prompts)} patient topics...")
    raw_classes = llm_client.generate_batch(
        class_prompts, max_tokens=2600, desc="Grounding — relationship classification",
        system_prompt=_CLASSIFY_SYSTEM, stop=None, enable_thinking=False,
    )
    classifications: Dict[str, Dict[str, dict]] = {}
    for t, raw in zip(topics, raw_classes):
        cands = candidates_by_topic[t["master_label"]][:12]
        if _is_admin_patient_topic(t):
            # Administrative/boilerplate topic — no clinical subject to enrich.
            cl = {
                c["master_label"]: {
                    "status": "NO_MATCH",
                    "confidence": 0.0,
                    "match_reason": "administrative/boilerplate patient topic — not clinically enriched",
                    "required_context": [], "additional_context": [],
                    "relevant_portions": [], "validated_context": [],
                    "rejected_context": [],
                }
                for c in cands
            }
        else:
            cl = _parse_classify_output(raw, cands)
            cl = validate_patient_context(cl, context_index)
        classifications[t["master_label"]] = cl
        accepted = {k: v for k, v in cl.items() if v["status"] != "NO_MATCH"}
        log.info(f"{t['master_label']}: {len(accepted)}/{len(cl)} accepted")

    # 5. Attach the ORIGINAL stored summary to every accepted candidate — the
    #    existing summary is the knowledge artifact; the LLM's matching decision
    #    is the routing layer, it does NOT rewrite the curated content.
    _load_guideline_summary_store()

    # 6. Assemble §22 records + §31 log
    for t in topics:
        cl = classifications[t["master_label"]]
        matched = []
        guid_sources: List[str] = []
        for cand in candidates_by_topic[t["master_label"]][:12]:
            entry = cl.get(cand["master_label"])
            if not entry or entry["status"] == "NO_MATCH":
                continue
            summary = _original_summary_for(cand["master_label"])
            matched.append({
                "master_label": cand["master_label"],
                "match_type": entry["status"],
                "confidence": entry["confidence"],
                "score": _candidate_score(cand),
                "match_reason": entry["match_reason"],
                "context_requirements": entry.get("required_context", []),
                "validated_context": entry.get("validated_context", []),
                "rejected_context": [c for c, _s in entry.get("rejected_context", [])],
                "source_docs": cand.get("source_docs", []),
                "summary": summary,
            })
            guid_sources.extend(cand.get("source_docs", []))
            log_entries.append({
                "patient_topic": t["master_label"],
                "candidate_guideline_topic": cand["master_label"],
                "retrieval_score": cand.get("local_score"),
                "reranker_score": cand.get("rerank_score"),
                "match_type": entry["status"],
                "match_reason": entry["match_reason"],
                "required_context": entry.get("required_context", []),
                "validated_context": entry.get("validated_context", []),
                "rejected_context": [c for c, _s in entry.get("rejected_context", [])],
                "selected_source_documents": cand.get("source_docs", []),
                "selected_chunks": [cand["chunk_id"]] if cand.get("chunk_id") else [],
                "summary_attached": bool(summary),
            })

        status = _final_status(cl)
        patient_body = t["body"] or t["summarized_description"] or ""
        results[t["master_label"]] = {
            "master_label": t["master_label"],
            "original_summary": patient_body,
            "grounding": {
                "status": status,
                "matched_guideline_topics": matched,
            },
            "grounded_summary": patient_body,  # patient content verbatim — no rewrite
            "provenance": {
                "patient_sources": t["source_docs"],
                "guideline_sources": sorted(set(guid_sources)),
            },
        }

    # 7. Fuse patient topic + matched guideline knowledge — weave the matched
    #    ORIGINAL guideline summaries into the patient's own summary (patient-first,
    #    attributed, additive). Only DIRECT_MATCH / PARTIAL_MATCH feed the fuse;
    #    CONTEXTUAL_MATCH stays visible in Grounding but is not woven in.
    import llm_client as _llm_client
    fuse_topics = []
    for t in topics:
        res = results[t["master_label"]]
        fuseable = [m for m in res["grounding"]["matched_guideline_topics"]
                    if m["match_type"] in ("DIRECT_MATCH", "PARTIAL_MATCH")]
        # Cap fuse input to the top-K most relevant matches by score — lower-
        # scored (but distinct) guidance stays visible in the Grounding section
        # without burning input tokens in every fuse prompt.
        fuseable = sorted(fuseable, key=lambda m: -(m.get("score") or 0))[:4]
        res["fused_summary"] = ""
        res["fuse_source_labels"] = [m["master_label"] for m in fuseable]
        if fuseable:
            fuse_topics.append((t, fuseable))
    if fuse_topics:
        fuse_prompts = [_build_fuse_prompt(t, m) for t, m in fuse_topics]
        log.info(f"Fusing patient topic + guideline knowledge for {len(fuse_prompts)} topics...")
        raw_fused = _llm_client.generate_batch(
            fuse_prompts, max_tokens=2500, desc="Grounding — patient-grounded summary fusion",
            system_prompt=_FUSE_SYSTEM, stop=None, enable_thinking=False,
        )
        for (t, _m), raw in zip(fuse_topics, raw_fused):
            fused = (raw or "").strip()
            if not fused:
                fused = t["body"] or t["summarized_description"] or ""
                log.warning(f"{t['master_label']}: fusion returned empty — keeping original topic verbatim")
            # Drop any leading label/heading the model re-emits — the output
            # already sits under the topic's own '#' heading.
            fused_lines = fused.splitlines()
            while fused_lines and (
                fused_lines[0].startswith("#") or re.match(r"^\s*label\s*:", fused_lines[0], re.IGNORECASE)
            ):
                fused_lines.pop(0)
            fused = "\n".join(fused_lines).strip()
            if _is_truncated(fused):
                log.warning(
                    f"{t['master_label']}: fusion output truncated (provider output cap) — "
                    f"retrying via Groq for full-length output"
                )
                fused = _retry_fusion_groq(
                    _build_fuse_prompt(t, _m), fused, system_prompt=_FUSE_SYSTEM,
                ) or fused
            deduped = _dedupe_fused_bullets(fused)
            if deduped.strip():
                fused = deduped
            results[t["master_label"]]["fused_summary"] = fused
    # Topics with nothing to fuse keep their own summary as the fused summary.
    for t in topics:
        res = results[t["master_label"]]
        if not res.get("fused_summary"):
            res["fused_summary"] = t["body"] or t["summarized_description"] or ""
        res["grounded_summary"] = res["fused_summary"]

    _write_outputs(output_dir, topics, results, log_entries)

    n_patched = _patch_nested_json(nested_path, results)
    log.info(f"Patched {n_patched} topics in {nested_path} (additive guideline_grounded_summary)")

    accepted_count = sum(1 for r in results.values() if r["grounding"]["status"] != "NO_MATCH")
    log.info(f"\n✅ Guideline grounding complete — {accepted_count}/{len(results)} "
             f"topics enriched → {output_dir}")
    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Patient–Guideline semantic enrichment engine")
    parser.add_argument("--patient-corpus-id", default=os.environ.get("STAGE2_PATIENT_ID", "default"))
    parser.add_argument("--limit", type=int, default=0, help="Only process first N patient topics")
    parser.add_argument("--no-llm", action="store_true", help="Retrieval only — no LLM classification/fusion")
    args = parser.parse_args()
    run_guideline_grounding(args.patient_corpus_id, limit=args.limit, no_llm=args.no_llm)


if __name__ == "__main__":
    main()