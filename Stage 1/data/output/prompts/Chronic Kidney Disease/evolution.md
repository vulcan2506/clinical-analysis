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

TOPIC: use of SGLT2 inhibitors in CKD

OUTPUT:
{{
  "feature_name": "use of SGLT2 inhibitors in CKD",
  "foundation": "The 2012 guideline recommended SGLT2 inhibitors only for patients with type 2 diabetes and CKD with albuminuria and eGFR \u226530.",
  "value_added": [
    "Expanded indication to include non-diabetic CKD patients",
    "Lowered eGFR threshold to \u226520 mL/min/1.73m\u00b2",
    "Added broader cardiovascular benefits beyond kidney outcomes"
  ],
  "narrative": "The 2012 guideline established SGLT2 inhibitors as kidney-protective therapy in a narrow diabetic CKD population. Subsequent trials demonstrated benefits across CKD stages and diabetes status, enabling the 2024 guideline to broaden eligibility and lower the eGFR threshold, thereby increasing the number of patients who may benefit from this therapy."
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