"""
hierarchy_topic_merge.py
────────────────────────
Merges the dense, source-scoped topic summaries (topic_summarizer.py output)
into the shallow hierarchy summaries (hierarchy_summarizer.py output).

The hierarchy summaries currently list each topic as a level-3 heading with
its summary paragraph beneath it:
    ### Topic Label

    <summarized_description>

That summary is shallow compared to the full topic summary, which holds the
real content: `# label`, `*Source:*`, **Clinical Recommendation**, **Key
Clinical Actions**, **Patient Preparation / Diagnostic Criteria**, etc.

This script appends the FULL content of every matching topic-summary file
(one per source doc) directly after that topic's summary paragraph — pure
copy, no rephrasing, no LLM calls. Matching is by `_slugify(label)` (the
same slug function topic_summarizer.py uses to name its output files), so
every topic that has a topic summary gets its '### Source N:' sections
spliced in underneath its summary.

It also completes truncated summary paragraphs: the summary text comes from
`summarized_description`, which topic_summarizer.py caps at
BLEND_CHAR_BUDGET (500 chars) and can therefore end mid-sentence. When a
topic's summary is a strict prefix of the topic's full cross-source blend
(recovered from `grounded_summary` in the nested taxonomy JSON), the
missing tail is appended — addition of text, no removal.

Writes:
  - data/output/hierarchy_summaries/<parent_slug>.md rewritten in place
    (existing structure untouched except the appended topic content).
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

import config
from topic_summarizer import _slugify, _split_brackets, _strip_leading_label

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

HIERARCHY_DIR = config.OUTPUT_DIR / "hierarchy_summaries"
TOPIC_DIR     = config.OUTPUT_DIR / "topic_summaries"
NESTED_PATH   = config.NESTED_OUTPUT_PATH

# A topic heading in a hierarchy summary looks like "### Label" alone on a
# line (rendered by hierarchy_summarizer.py), with the summary paragraph on
# the following non-empty line. The "### Source N: <doc>" lines the merge
# itself appends match the same shape but are filtered out by the slug check.
_TOPIC_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")


def _parse_topic_block(content: str) -> Tuple[str, str]:
    """Split a topic-summary file into (source_doc, body).

    Topic-summary files are rendered by topic_summarizer.py as:

        # <label>
        *Source: <doc>*

        **Clinical Recommendation:** ...
        ...

    The '*Source:' line may itself span several physical lines when the doc
    name is long, so we keep consuming lines until one ends with a '*'.
    Returns the raw doc name (with the markdown markers stripped) and the
    body text (everything after the source line, trimmed).
    """
    lines = content.splitlines()
    # Skip the leading '# <label>' line.
    i = 0
    while i < len(lines) and not lines[i].strip().startswith("*Source:"):
        i += 1
    if i >= len(lines):
        return "", "\n".join(lines).strip()

    # Collect the source name: starts on the '*Source:' line and continues
    # until a physical line whose text ends with '*'.
    doc_lines: List[str] = []
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("*Source:"):
            ln = ln[len("*Source:"):].strip()
        doc_lines.append(ln)
        i += 1
        if ln.rstrip().endswith("*"):
            break
    doc = " ".join(d.rstrip("*").strip() for d in doc_lines if d).strip()
    body = lines[i:]
    return doc, "\n".join(body).strip()


def _build_topic_index() -> Dict[str, List[Path]]:
    """Map topic slug → list of matching topic-summary files (sorted by
    source dir then filename for deterministic output)."""
    index: Dict[str, List[Path]] = {}
    for p in sorted(TOPIC_DIR.glob("*/*.md")):
        index.setdefault(p.stem, []).append(p)
    return index


def _build_full_blend_index() -> Dict[str, str]:
    """Map topic slug → the untruncated cross-source combined summary.

    The summary text in a hierarchy summary comes from `summarized_description`,
    which topic_summarizer.py caps at BLEND_CHAR_BUDGET (500 chars) and can
    therefore end mid-sentence. The complete text is recoverable from the
    nested taxonomy's `grounded_summary` (one bracketed embed-text per source
    doc): joining the brackets back reproduces exactly what `_blend()` joins
    before truncating. Used to append the missing tail to truncated summaries.
    Leading label repetitions are stripped, matching what the renderer emits.
    """
    index: Dict[str, str] = {}
    if not NESTED_PATH.exists():
        log.warning(f"Nested taxonomy JSON not found: {NESTED_PATH} — skipping summary completion")
        return index
    with open(NESTED_PATH, encoding="utf-8") as f:
        taxonomy = json.load(f)
    for parent in taxonomy.get("taxonomy", []):
        for sub in parent.get("sub_categories", []):
            for topic in sub.get("topics", []):
                label = topic.get("master_label", "")
                full = _strip_leading_label(
                    " ".join(_split_brackets(topic.get("grounded_summary", ""))), label)
                if label and full:
                    index.setdefault(_slugify(label), full)
    return index


def run_hierarchy_topic_merge() -> int:
    if not HIERARCHY_DIR.exists():
        log.error(f"Hierarchy summary dir not found: {HIERARCHY_DIR}")
        return 0

    index = _build_topic_index()
    full_blend = _build_full_blend_index()
    log.info(f"Indexed {sum(len(v) for v in index.values())} topic summaries "
             f"across {len(index)} topics; {len(full_blend)} full-blend texts")

    n_rendered = 0
    n_topics_appended = 0
    n_bullets_completed = 0
    for h_path in sorted(HIERARCHY_DIR.glob("*.md")):
        text = h_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        out: List[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _TOPIC_HEADING_RE.match(line)
            if not m:
                out.append(line)
                i += 1
                continue

            label = m.group(1).strip()
            slug  = _slugify(label)
            # Only treat the line as a topic heading if it is one — the
            # sub-category detail also uses bold "**Key Features:**"-style
            # section labels that match the same shape but are not topics.
            if slug not in index and slug not in full_blend:
                out.append(line)
                i += 1
                continue

            out.append(line)

            # The summary paragraph is the next non-empty line after the heading.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not lines[j].strip():
                log.warning(f"No summary body after '{label}' in {h_path.name}")
                i += 1
                continue
            # Preserve the blank separator line(s) between heading and body.
            out.extend(lines[i + 1:j])

            # Complete a truncated combined summary: if the paragraph text is a
            # strict prefix of the full blend, append the missing tail. Pure
            # addition — no existing text is removed.
            body_text = lines[j]
            full = full_blend.get(slug)
            if full and full.startswith(body_text.strip()) and len(full) > len(body_text.strip()):
                body_text = full
                n_bullets_completed += 1
            out.append(body_text)
            next_i = j + 1

            matches = index.get(slug, [])
            if not matches:
                log.warning(f"No topic summary for '{label}' (slug '{slug}') in {h_path.name}")
                i = next_i
                continue

            # Build one consolidated block: a '### Source N: <doc>' header +
            # body for each matching source file, with a blank line between
            # sources. The '### <label>' heading already sits above the topic's
            # summary paragraph (rendered by hierarchy_summarizer.py), so this
            # block starts directly with the source sections.
            block: List[str] = []
            appended_sources = 0
            for n, t_path in enumerate(matches, start=1):
                content = t_path.read_text(encoding="utf-8").rstrip("\n")
                doc, body = _parse_topic_block(content)
                if not body:
                    log.warning(f"Empty topic summary body in {t_path}")
                    continue
                if block:
                    block.append("")
                block.append(f"### Source {n}: {doc}")
                block.append("")
                block.append(body)
                appended_sources += 1

            if appended_sources == 0:
                i = next_i
                continue

            consolidated = "\n".join(block)
            # Idempotency guard: skip if this consolidated block is already
            # present (re-running the merge must not duplicate).
            if consolidated not in text:
                out.append("")
                out.append(consolidated)
                out.append("")
                n_topics_appended += 1
            i = next_i

        if out != lines:
            h_path.write_text("\n".join(out) + "\n", encoding="utf-8")
            n_rendered += 1

    log.info(f"Merged topic summaries into {n_rendered} hierarchy summaries "
             f"({n_topics_appended} topic-summary blocks appended; "
             f"{n_bullets_completed} truncated bullets completed) → {HIERARCHY_DIR}")
    return n_rendered


def main():
    run_hierarchy_topic_merge()


if __name__ == "__main__":
    main()
