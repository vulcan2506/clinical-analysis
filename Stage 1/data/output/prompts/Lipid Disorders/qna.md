Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the 2025 guideline add bempedoic acid as a recommended therapy for statin-intolerant patients?", "a": "The Task Force incorporated new randomized trial data demonstrating that bempedoic acid significantly lowers LDL-C with a favorable safety profile in patients unable to tolerate statins. This evidence base was deemed sufficient to support a formal recommendation, particularly for those at high or very high cardiovascular risk."}},
  {{"q": "How should clinicians interpret the new recommendation to measure lipoprotein(a) in primary prevention?", "a": "The guideline now suggests measuring Lp(a) once in adults, especially those with a family history of premature ASCVD, to refine risk beyond traditional factors. This is not intended as a screening test for all, but as a targeted assessment to personalize prevention strategies, particularly when LDL-C is borderline or risk remains uncertain."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):