Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the 2024 guideline expand the use of SGLT2 inhibitors to non-diabetic CKD patients?", "a": "The 2024 KDIGO guideline incorporated new trial data showing cardiovascular and kidney benefits of SGLT2 inhibitors in patients with CKD regardless of diabetes status. This evidence base grew significantly after the prior guideline version, supporting broader indications."}},
  {{"q": "How should clinicians interpret the change in blood pressure targets for CKD patients with diabetes?", "a": "The updated target of <140 mmHg reflects a shift toward individualized care based on cardiovascular risk stratification. Clinicians should assess overall cardiovascular risk and consider reserving <130 mmHg for those at highest risk, balancing potential harms like hypotension and falls."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):