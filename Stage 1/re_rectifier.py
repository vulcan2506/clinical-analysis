"""
re_rectifier.py
───────────────
LLM-based post-processing for tables via local llama.cpp server.
"""

import os
import re
import json
import logging
from typing import Dict, List

import config
import llm_client

log = logging.getLogger(__name__)


def unload():
    pass


# ── NO .format() TO AVOID KEY ERRORS ──────────────────────────────────────────

_FIX_PROMPT_BASE = """You are an expert data cleaner. Fix this JSONL table based on the Original Markdown Table.

CRITICAL RULES:
1. Output ONLY valid JSON Lines (JSONL). No markdown formatting blocks, no preamble.
2. DO NOT HALLUCINATE: If a cell is empty in the markdown, do NOT invent data or copy the category name to fill it.
3. RECOVER MISSING DATA: If the Markdown has values (e.g., 'Yes') that are missing from the Broken JSONL, add them back using a logical key (e.g., "Status": "Yes").
4. DROP IRRELEVANT KEYS: If a row is a simple checklist item (like "Changes to Server?"), DO NOT include keys from other tables (like "Change Description"). Just use keys that make sense for that row.
5. INFER MISSING HEADERS: If the Original Markdown table has blank headers (e.g. `| Date | | |`), you MUST invent logical header names based on the content of the columns (e.g., "Description", "Affected Section", "Reference"). Do NOT use empty strings ("") as keys.
6. FIX ROW-SHIFTED PARALLEL COLUMNS: Some tables have two parallel value-groups sharing one row grid (e.g. a percentage column next to an absolute-count column, each row also carrying its own range). If a cell holds two distinct values crammed together (e.g. "500 100-600" — a count AND a range in one cell), the second group's column is misaligned by one row: row 1's cell for that column is usually the group's own header label (not a value), and every following row shows the value that actually belongs to the row ABOVE it. Use the Original Markdown's row order to work out which value truly belongs to which row, split the crammed cell into its own key(s), and shift the whole column up by one row so each row's second-group value matches that row's own label. The last row will end up with nothing to shift into — leave those keys empty for it, do not invent a value.

--- EXAMPLES ---
EXAMPLE 1: Glued Checklists
ORIGINAL MARKDOWN:
| Date | Change Description |
|---|---|
| 2026-03-13 | Runtime properties enhanced |
| Release Summary | |
| New Features? | Yes |

BROKEN JSONL:
{"Date": "2026-03-13", "Change Description": "Runtime properties enhanced"}
{"Date": "New Features?", "Change Description": "New Features?", "Column_3": ""}

FIXED JSONL:
{"Date": "2026-03-13", "Change Description": "Runtime properties enhanced"}
{"Category": "New Features?", "Status": "Yes"}

EXAMPLE 2: Blank Headers
ORIGINAL MARKDOWN:
| Date | | |
|---|---|---|
| 08/28/2025 | Added Error Handling Details | See Integer Validation |

BROKEN JSONL:
{"Date": "08/28/2025", "": "Added Error Handling Details", "Column_3": "See Integer Validation"}

FIXED JSONL:
{"Date": "08/28/2025", "Description": "Added Error Handling Details", "Reference": "See Integer Validation"}"""


# ── Value-shift detection ────────────────────────────────────────────────────
# _needs_fixing() below only ever looked at KEY hygiene (empty/Column_N/dup
# keys) — a table with clean, well-named headers but silently row-shifted
# VALUES (e.g. a twin-parallel-column layout where OCR misaligns the second
# group by one row) sailed through untouched. This catches that case via a
# cheap, domain-general syntactic signal: a cell holding two distinct
# value-like tokens crammed together (e.g. "7716 /cmm 2000 - 6700") usually
# means a column that should span two rows collapsed into one — the table-
# structure step merged what should've been separate cells. Not specific to
# any one document type; the same pattern shows up whenever a table has two
# parallel value-groups sharing one row grid.
_RANGE_RE = re.compile(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?")


def _cell_has_crammed_values(value: str) -> bool:
    m = _RANGE_RE.search(value)
    if not m:
        return False
    before = value[:m.start()].strip()
    return bool(before) and any(ch.isdigit() for ch in before)


def _looks_value_shifted(rows: List[Dict]) -> bool:
    return any(
        _cell_has_crammed_values(str(v))
        for row in rows
        for v in row.values()
    )


def _needs_fixing(jsonl_text: str) -> bool:
    """Return True if JSONL has empty keys, Column_N keys, duplicate keys, or
    a value-shift signal (see _looks_value_shifted)."""
    rows = []
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
            rows.append(row)
            for k in row:
                k_s = str(k).strip()
                if not k_s or re.match(r"^Column_\d+$", k_s, re.IGNORECASE):
                    return True
        except json.JSONDecodeError:
            return True
    return _looks_value_shifted(rows)


def _build_prompt(raw_markdown: str, jsonl_data: str) -> str:
    return (
        f"{_FIX_PROMPT_BASE}\n\n"
        f"--- ACTUAL TASK ---\n"
        f"ORIGINAL MARKDOWN TABLE:\n{raw_markdown}\n\n"
        f"BROKEN JSONL TABLE:\n{jsonl_data}\n\n"
        f"FIXED JSONL:\n"
    )


def _count_markdown_data_rows(raw_md: str) -> int:
    """Count pipe-table rows in the ORIGINAL markdown, excluding the header
    separator (---) line — the ground-truth row count a correct fix should
    roughly preserve."""
    return sum(
        1 for ln in raw_md.splitlines()
        if (s := ln.strip()).startswith("|") and s.endswith("|")
        and not re.match(r"^\|[\s\-:|]+\|$", s)
    )


def _purge_empty_keys(rows: List[Dict]) -> List[Dict]:
    if not rows: return rows
        
    all_keys = set()
    for r in rows: all_keys.update(r.keys())
        
    valid_keys = set()
    for k in all_keys:
        k_clean = str(k).strip()
        if not k_clean or re.match(r"^Column_\d+$", k_clean, re.IGNORECASE):
            continue
        if any(str(r.get(k, "")).strip() for r in rows):
            valid_keys.add(k)
            
    cleaned_rows = []
    for r in rows:
        clean_row = {k: v for k, v in r.items() if k in valid_keys and str(v).strip() != ""}
        if clean_row: cleaned_rows.append(clean_row)
        
    return cleaned_rows


def run_re_rectifier(chunks: List[Dict], batch_size: int = 8) -> List[Dict]:
    target_indices = [i for i, c in enumerate(chunks) if c.get("md_file_path")]
    
    if not target_indices:
        log.info("[Re-Rectifier] No raw markdown files found. Skipping.")
        return chunks

    # Pre-filter: only send tables that actually have broken JSONL
    broken_indices = [i for i in target_indices if _needs_fixing(chunks[i].get("text", ""))]
    skipped = len(target_indices) - len(broken_indices)
    log.info(f"[Re-Rectifier] {len(broken_indices)}/{len(target_indices)} tables need fixing ({skipped} skipped — already clean)")

    if not broken_indices:
        return chunks

    prompts = []
    valid_targets = []
    raw_md_by_target: Dict[int, str] = {}

    for i in broken_indices:
        md_path = chunks[i]["md_file_path"]
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                raw_md = f.read()
            prompts.append(_build_prompt(raw_md, chunks[i]["text"]))
            valid_targets.append(i)
            raw_md_by_target[i] = raw_md
        except Exception as e:
            log.error(f"Error preparing prompt for {md_path}: {e}")

    # A flat 600-token cap silently truncated larger tables mid-row (e.g. a
    # 25-row CBC panel got cut off after row 12, losing real data with no
    # error raised — STOP_JSON never fired because the output was cut off
    # before hitting a stop sequence, not because it finished cleanly).
    # Size the shared batch ceiling off the largest prompt actually being
    # sent, since generate_local_batch() takes one max_tokens for the whole
    # batch — smaller tables just get unused headroom, not forced length.
    # ratio=0.8 (not the usual >1 extraction/classification ratio) because
    # this task's output is comparable to or LARGER than its input — each
    # row often gains keys (e.g. RULE 6 splitting one crammed cell into two),
    # and JSON syntax overhead (quotes/braces/colons) adds more per row than
    # the markdown pipes it replaces. Measured against the real 26-row CBC
    # table: ratio=2.5/floor=600 undershot to 600 (needed ~1500+) and
    # silently truncated 12 rows.
    batch_max_tokens = max(
        (llm_client.budget(p, ratio=0.8, floor=900, ceil=3000) for p in prompts),
        default=900,
    )
    llm_outputs = llm_client.generate_local_batch(
        prompts, max_tokens=batch_max_tokens,
        desc="Re-Rectification", stop=llm_client.STOP_JSON,
        enable_thinking=False,
    )

    for idx, raw_out in zip(valid_targets, llm_outputs):
        clean = re.sub(r"```(?:jsonl|json)?|```", "", raw_out).strip()
        
        parsed_rows = []
        for line in clean.splitlines():
            line = line.strip().strip(",")
            if line.startswith("{") and line.endswith("}"):
                try: parsed_rows.append(json.loads(line))
                except json.JSONDecodeError: pass
                
        parsed_rows = _purge_empty_keys(parsed_rows)

        if parsed_rows:
            chunks[idx]["text"] = "\n".join(json.dumps(r, ensure_ascii=False) for r in parsed_rows)
            chunks[idx]["_re_rectified"] = True
            # The row-shift fix (RULE 6 in _FIX_PROMPT_BASE) is a genuinely
            # hard reasoning task for the model — don't just trust the output.
            # Two independent checks, since they catch different failure
            # modes: _looks_value_shifted catches the fix not actually
            # resolving the crammed-cell signal; _count_markdown_data_rows
            # catches a row COUNT mismatch against the source table — cause
            # unknown from this signal alone (could be max_tokens truncation,
            # could be the model dropping/merging a row) — either way,
            # something narrowed the table and a well-formed JSONL row count
            # won't reveal it on its own, so this is flagged for a human to
            # actually look at rather than guessed at here.
            original_row_count = _count_markdown_data_rows(raw_md_by_target.get(idx, ""))
            row_count_mismatch = original_row_count > 0 and len(parsed_rows) < original_row_count - 1
            if _looks_value_shifted(parsed_rows) or row_count_mismatch:
                chunks[idx]["_needs_human_review"] = True
            if row_count_mismatch:
                log.warning(
                    f"[Re-Rectifier] chunk {chunks[idx].get('chunk_id')} — fix produced "
                    f"{len(parsed_rows)} rows vs ~{original_row_count} in the source table "
                    f"— flagged for review"
                )

        try:
            if os.path.exists(chunks[idx]["md_file_path"]):
                os.remove(chunks[idx]["md_file_path"])
        except Exception as e:
            log.warning(f"Failed to delete {chunks[idx]['md_file_path']}: {e}")
            
        del chunks[idx]["md_file_path"]

    return chunks