Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why would a clinician choose early insulin initiation over oral agents in newly diagnosed type 2 diabetes?", "a": "Early insulin initiation may be preferred in patients with severe hyperglycemia or symptoms to rapidly achieve glycemic control and reduce complications. The guideline now recognizes this as a viable first-line option in select cases, particularly when oral agents are unlikely to achieve targets quickly."}},
  {{"q": "How should glycemic targets be adjusted for elderly patients with multiple comorbidities?", "a": "The guideline recommends individualized targets for elderly patients, prioritizing avoidance of hypoglycemia over strict glycemic control. Targets should be relaxed based on patient frailty, comorbidities, and risk of adverse events rather than applying uniform standards."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):