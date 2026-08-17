Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why would the protocol exclude chloride from the electrode slope acceptance criteria?", "a": "Chloride electrodes typically have different performance characteristics compared to other ion selective electrodes. The protocol likely separates chloride to account for its unique measurement challenges and to allow for more appropriate acceptance criteria that reflect its specific behavior in clinical testing."}},
  {{"q": "What are the implications of allowing specimens to reach room temperature during preparation?", "a": "This change accommodates standard laboratory workflows where immediate processing isn't always possible. The protocol ensures specimen integrity by requiring the unused portion to be returned to frozen storage, maintaining the cold chain for the remaining sample while allowing necessary preparation steps at room temperature."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):