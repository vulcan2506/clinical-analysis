You are {analyst_role} writing a "value-add" note about a change in {doc_purpose} for the feature "{topic}". You already have structured profiles for both versions and a classified delta below — do NOT re-read raw text, just re-synthesize these facts into a constructive evolution narrative.

CRITICAL RULES:
- Output ONLY a single JSON object — nothing before or after it.
- Ground every statement in the profiles/delta below — do not invent facts.
- Max 5 items in value_added. Close all braces.

Output this exact structure with real values:

{{
  "feature_name": "<short descriptive name>",
  "foundation": "<one sentence: what {vA} established>",
  "value_added": [
    "<concrete capability {vB} adds on top of that foundation>",
    "<another — max 5 total>"
  ],
  "narrative": "<2-3 sentences: {vA} introduced X; {vB} builds on it by Y, enabling Z>"
}}

### EXAMPLE ###

TOPIC: automated differential leukocyte counting

OUTPUT:
{{
  "feature_name": "automated differential leukocyte counting",
  "foundation": "Older hematology analyzers classified white blood cells into three broad categories using impedance and basic optical scatter.",
  "value_added": [
    "five-part differential with improved accuracy",
    "flagging of abnormal or immature cells",
    "reduced need for manual smear review in many cases"
  ],
  "narrative": "The foundational technology grouped WBCs coarsely, limiting diagnostic precision. The newer generation builds on this by using multi-angle light scatter and fluorescent markers to resolve five distinct populations and detect pathological variants, significantly enhancing clinical utility and reducing labor-intensive manual review."
}}

### ACTUAL TASK ###
TOPIC: {topic}

{vA} profile:
{profile_A}

{vB} profile:
{profile_B}

Delta analysis: {delta_analysis}
Key differences: {key_differences}

OUTPUT (JSON only — close all braces, max 5 items in value_added):