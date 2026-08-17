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

TOPIC: Statin Therapy Initiation Thresholds

OUTPUT:
{{
  "feature_name": "Statin Therapy Initiation Thresholds",
  "foundation": "Prior guidelines recommended statin therapy for patients with LDL-C \u2265190 mg/dL or clinical ASCVD, with consideration for diabetes or 10-year ASCVD risk \u22657.5%.",
  "value_added": [
    "Lowered LDL-C initiation threshold to \u2265160 mg/dL",
    "Reduced ASCVD risk threshold for consideration to \u22655%",
    "Expanded eligibility for primary prevention in lower-risk populations"
  ],
  "narrative": "The 2026 guideline builds upon prior recommendations by incorporating newer trial data showing benefit from earlier statin initiation. By lowering the LDL-C threshold and ASCVD risk threshold, the guideline enables earlier intervention in patients who may benefit from preventive therapy, potentially reducing long-term cardiovascular events."
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