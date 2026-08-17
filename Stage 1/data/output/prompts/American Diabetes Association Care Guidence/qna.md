Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the ADA Standards of Care 2026 expand SGLT2 inhibitor recommendations beyond glycemic control?", "a": "The expansion reflects new cardiovascular outcome trial evidence demonstrating that SGLT2 inhibitors reduce heart failure hospitalization and cardiovascular mortality in patients with type 2 diabetes, independent of their glucose-lowering effects. This led to a broader indication for cardiovascular protection."}},
  {{"q": "How should clinicians approach A1C targets in older adults according to the 2026 Standards of Care?", "a": "Clinicians should abandon fixed A1C targets for older adults and instead use individualized goals based on comprehensive geriatric assessment including life expectancy, cognitive status, functional independence, and hypoglycemia risk. The focus shifts from glycemic control to overall health and quality of life outcomes."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):