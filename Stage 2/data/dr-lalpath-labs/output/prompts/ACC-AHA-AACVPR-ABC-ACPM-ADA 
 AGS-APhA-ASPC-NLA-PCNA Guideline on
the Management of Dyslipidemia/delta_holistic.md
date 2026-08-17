You are {analyst_role}. Read the {doc_purpose} text below about "{topic}" and extract a structured behavioral profile.

CRITICAL RULES:
- Output ONLY a single JSON object — nothing before or after it.
- Do NOT output the structure definition itself; fill in real values.
- Keep each list to a MAXIMUM of 5 items so the JSON stays concise.
- If the text covers multiple features, combine their behaviors under one   profile using a descriptive feature_name like "Feature A + Feature B".
- Use exact property names where mentioned (e.g. WORKBASKET_RULE_OPTIMIZATION_ENABLE).
- If a list has no items write [].
- Complete the closing braces — never leave the JSON unfinished.

Output this exact structure with real values:

{{
  "feature_name": "<short descriptive name — combine if multiple features>",
  "key_behaviors": [
    "<behavior 1>",
    "<behavior 2>",
    "<behavior 3 — max 5 total>"
  ],
  "requirements": [
    "<a clinical prerequisite or mandatory condition specified in the guideline, such as patient preparation for lipid testing or specific diagnostic criteria — max 5 total>"
  ],
  "deprecated_items": [
    "<a previously recommended clinical practice, measurement, or intervention that is no longer endorsed or has been superseded by newer evidence — max 5 total>"
  ],
  "new_items": [
    "<a newly introduced clinical recommendation, measurement technique, risk factor, or therapeutic option endorsed in the updated guideline — max 5 total>"
  ],
  "patient_value": "<patient's actual measured result with units, e.g. '200.00 pg/mL'. NULL if text has no patient-specific value>",
  "reference_range": "<normal/reference range for this test, e.g. '211.00 - 911.00 pg/mL'. NULL if no reference range>",
  "interpretation": "<clinical interpretation: is the value normal/low/high? What does it mean? NULL if no clinical context>",
  "significance_level": "<clinical urgency: 'critical' | 'high' | 'moderate' | 'low' | 'informational'. NULL for non-clinical content>"
}}

### EXAMPLE 1 ###

TOPIC: Statin Therapy Initiation Thresholds
TEXT: Statin therapy is now recommended for patients with LDL-C ≥160 mg/dL or 10-year ASCVD risk ≥5%, expanding initiation to lower-risk and lower-LDL-C populations.

OUTPUT:
{{
  "feature_name": "Statin Therapy Initiation Thresholds",
  "key_behaviors": ["Statin therapy is now recommended for patients with LDL-C \u2265160 mg/dL or 10-year ASCVD risk \u22655%, expanding initiation to lower-risk and lower-LDL-C populations."],
  "requirements": [],
  "deprecated_items": [],
  "new_items": []
}}

### EXAMPLE 2 ###

TOPIC: Lp(a) Risk Stratification
TEXT: Elevated Lp(a) ≥50 mg/dL is now a threshold for intensifying statin therapy or considering PCSK9 inhibitor therapy in high-risk patients.

OUTPUT:
{{
  "feature_name": "Lp(a) Risk Stratification",
  "key_behaviors": ["Elevated Lp(a) \u226550 mg/dL is now a threshold for intensifying statin therapy or considering PCSK9 inhibitor therapy in high-risk patients."],
  "requirements": [],
  "deprecated_items": [],
  "new_items": []
}}

### ACTUAL TASK ###
TOPIC: {topic}
TEXT:
{text}

IMPORTANT — GUIDELINE GROUNDING:
If the text above contains a section labeled "MATCHED CLINICAL GUIDELINES", you MUST use it to ground your extraction:
- The "interpretation" field must reference the guideline's classification   thresholds, risk categories, or clinical recommendations — not just state   "below/above reference range".
- The "key_behaviors" must include at least one guideline-sourced clinical   fact (e.g. "Per [Guideline Name]: levels below X indicate Y risk").
- The "patient_value" must show the patient's actual result, and the   "interpretation" must explain where it falls within the guideline's   classification (e.g. "Patient's 200.00 pg/mL falls in the Deficient   range per the guideline (<50 nmol/L for Vitamin D)").

OUTPUT (JSON only — close all braces, max 5 items per list, include patient_value/reference_range/interpretation if the text contains a lab result):