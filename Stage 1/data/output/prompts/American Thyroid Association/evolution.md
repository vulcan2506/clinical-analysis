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

TOPIC: TSH suppression targets

OUTPUT:
{{
  "feature_name": "TSH suppression targets",
  "foundation": "The earlier guideline recommended TSH suppression to below the reference range for all intermediate-risk differentiated thyroid cancer patients.",
  "value_added": [
    "Introduction of patient-specific factors to guide TSH suppression targets",
    "Emphasis on balancing therapeutic benefit against potential adverse effects",
    "Consideration of age, comorbidities, and disease risk in treatment decisions"
  ],
  "narrative": "The earlier guideline established a uniform approach to TSH suppression for intermediate-risk patients. The updated version builds on this foundation by introducing nuanced, individualized recommendations that account for patient-specific factors. This evolution enables more tailored treatment approaches that optimize benefit-risk ratios, particularly for older patients or those with significant comorbidities where aggressive suppression may be harmful."
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