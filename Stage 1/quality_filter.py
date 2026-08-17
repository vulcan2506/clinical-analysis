"""
quality_filter.py
─────────────────
Drops chunks that have a text_quality_score below a strict threshold (e.g. 70%).
Ensures downstream taxonomy isn't polluted with garbage data.

Exception: structured measurement tables with a numeric result and reference
range are always retained, even if their generic text-quality score is low.
These tables contain actual lab values (e.g. "Vitamin B12: 90.00 pg/mL")
that must not be filtered out by a quality heuristic designed for prose.
"""

import logging
import re
from typing import List, Dict

log = logging.getLogger(__name__)

# Matches structured measurement patterns like:
#   "Vitamin B12: 90.00 pg/mL"
#   "HbA1c = 10.0 %"
#   "TSH: 0.5 mIU/L (Ref: 0.4-4.0)"
_MEASUREMENT_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:[\w\s%/–-]+?)"           # test name (e.g. "Vitamin B12", "HbA1c")
    r"[:\s=]+\s*"
    r"(\d+\.?\d*)\s*"            # numeric value
    r"(?:[\w/%°C-]+?)"           # unit (e.g. "pg/mL", "%")
    r".*?"
    r"(?:Ref|Reference|Range)[:\s]*"
    r"(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)",  # reference range
    re.IGNORECASE | re.DOTALL,
)

# Simpler pattern: just a numeric value with units (catches more cases)
_NUMERIC_WITH_UNITS_RE = re.compile(
    r"\b\d+\.?\d+\s*(?:pg/mL|ng/mL|mg/dL|mmol/L|mIU/L|U/mL|%|μIU/mL)\b",
    re.IGNORECASE,
)


def _is_structured_measurement(chunk: Dict) -> bool:
    """
    Returns True if the chunk text contains a structured lab measurement
    (numeric result + reference range or units) that should be preserved
    regardless of the quality score.
    """
    text = chunk.get("text", "")
    if not text:
        return False

    # Check for explicit reference range pattern
    if _MEASUREMENT_RE.search(text):
        return True

    # Check for numeric value with medical units (at least 2 occurrences
    # suggests a table of measurements, not just a passing mention)
    numeric_matches = _NUMERIC_WITH_UNITS_RE.findall(text)
    if len(numeric_matches) >= 2:
        return True

    return False


def run_filter(chunks: List[Dict], threshold: float = 0.70) -> List[Dict]:
    initial_count = len(chunks)
    valid_chunks = []
    
    for c in chunks:
        # Default to 1.0 if the score key is completely missing so we don't accidentally drop good chunks
        score = c.get("text_quality_score", 1.0) 
        
        # Always keep structured measurement tables regardless of quality score
        if score >= threshold or _is_structured_measurement(c):
            valid_chunks.append(c)

    dropped_count = initial_count - len(valid_chunks)
    
    log.info(f"[Quality Filter] Dropped {dropped_count} chunks with quality score < {threshold*100}%.")
    log.info(f"[Quality Filter] Remaining high-quality chunks: {len(valid_chunks)}")
    
    return valid_chunks