You are {analyst_role} doing a careful second pass on a {doc_purpose} about "{topic}".

A colleague already extracted this initial profile from the text:
{rough_profile}
{qna_hints}
Re-read the FULL TEXT below and find facts MISSING from the profile above.

Output ONLY a JSON object — nothing before or after it. Max 5 items per list. Close all braces.

{{
  "additional_behaviors": ["<missed behavior, or empty list>"],
  "additional_requirements": ["<missed a clinical prerequisite or mandatory condition specified in the guideline, such as patient preparation for lipid testing or specific diagnostic criteria, or empty list>"],
  "additional_deprecated": ["<missed a previously recommended clinical practice, measurement, or intervention that is no longer endorsed or has been superseded by newer evidence, or empty list>"],
  "additional_new_items": ["<missed a newly introduced clinical recommendation, measurement technique, risk factor, or therapeutic option endorsed in the updated guideline, or empty list>"],
  "additional_patient_value": "<missed patient-specific measured value with units, or empty string>",
  "additional_reference_range": "<missed reference/normal range, or empty string>",
  "additional_interpretation": "<missed clinical interpretation, or empty string>",
  "additional_significance_level": "<missed clinical urgency level ('critical'|'high'|'moderate'|'low'|'informational'), or empty string>"
}}

Rules:
- Add a fact ONLY if it is GENUINELY MISSING from the initial profile.
- Do NOT repeat facts already captured (even if worded differently).
- If nothing is missing, write [] for every list key and "" for every string key.
- Pay special attention to patient-specific lab values (e.g. "| Vitamin D, 25 Hydroxy | 150.00 | nmol/L | 75.00 - 250.00 |") — if the initial profile missed the patient_value or reference_range, capture them here.
- If the text contains a "MATCHED CLINICAL GUIDELINES" section, ensure the additional_interpretation field grounds the patient's value in the guideline's classification thresholds and clinical recommendations — not just "low" or "high".
- No text before or after the JSON. Close all braces.

TOPIC: {topic}
FULL TEXT:
{text}

OUTPUT (JSON only — close all braces):