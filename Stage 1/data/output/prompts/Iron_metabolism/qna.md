Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why does hepcidin have different effects on iron absorption in various tissues?", "a": "Hepcidin's tissue-specific effects arise from differences in ferroportin expression patterns and regulatory pathways. Intestinal cells show additional hepcidin-independent regulatory mechanisms that modulate iron absorption during inflammatory states."}},
  {{"q": "How should clinicians interpret iron absorption tests during acute inflammation?", "a": "Standard iron absorption tests may be misleading during inflammation because inflammatory cytokines can simultaneously increase hepcidin (which should decrease absorption) and activate alternative iron uptake pathways in specific cell types, creating a complex and variable response."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):