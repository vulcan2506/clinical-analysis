Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the Endocrine Society switch to the GRADE methodology for evidence appraisal?", "a": "The GRADE approach provides a standardized, transparent framework for assessing evidence quality and recommendation strength. It improves consistency across guidelines and helps clinicians better understand the reliability of recommendations."}},
  {{"q": "How does the inclusion of patient representatives change the guideline development process?", "a": "Patient representatives provide critical input on outcomes that matter to patients, ensure recommendations are patient-centered, and help identify potential barriers to implementation. Their inclusion improves the relevance and applicability of guidelines to real-world care."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):