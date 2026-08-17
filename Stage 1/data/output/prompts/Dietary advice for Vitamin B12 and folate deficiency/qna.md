Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why do proton pump inhibitors increase the risk of vitamin B12 deficiency?", "a": "Proton pump inhibitors suppress stomach acid production, which is necessary for releasing B12 from food proteins. Without adequate acid, the vitamin remains bound and unavailable for absorption in the small intestine."}},
  {{"q": "What cooking methods best preserve folate in vegetables?", "a": "Steaming or microwaving vegetables for short durations minimizes folate loss. Avoid boiling or prolonged hot holding, as folate is water-soluble and heat-sensitive."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):