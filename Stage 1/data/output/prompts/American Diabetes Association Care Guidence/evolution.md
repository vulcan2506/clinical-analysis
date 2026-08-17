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

TOPIC: SGLT2 inhibitor use in heart failure

OUTPUT:
{{
  "feature_name": "SGLT2 inhibitor use in heart failure",
  "foundation": "SGLT2 inhibitors were initially recommended for glycemic control in patients with type 2 diabetes and established cardiovascular disease.",
  "value_added": [
    "Expanded indication to include heart failure with reduced ejection fraction regardless of diabetes status",
    "New evidence supporting cardiovascular mortality reduction",
    "Inclusion in multiple cardiovascular guidelines beyond diabetes care"
  ],
  "narrative": "The 2026 Standards of Care builds on the 2025 foundation by extending SGLT2 inhibitor recommendations to broader heart failure populations. This evolution reflects growing evidence that these medications provide cardiovascular benefits independent of their glucose-lowering effects, transforming them from diabetes-specific agents to cardiovascular therapeutics with ancillary glycemic benefits."
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