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

TOPIC: insulin initiation timing

OUTPUT:
{{
  "feature_name": "insulin initiation timing",
  "foundation": "Version A required failure of maximum oral therapy before insulin initiation in type 2 diabetes",
  "value_added": [
    "Added early insulin initiation as an option for patients with marked hyperglycemia at diagnosis",
    "Recognized rapid glycemic control as beneficial in select cases",
    "Expanded therapeutic flexibility beyond stepwise approach"
  ],
  "narrative": "The original guideline established a stepwise therapeutic approach requiring oral therapy failure before insulin. The updated version builds on this foundation by introducing early insulin initiation for patients with severe hyperglycemia, enabling more rapid achievement of glycemic targets while maintaining individualized care principles."
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