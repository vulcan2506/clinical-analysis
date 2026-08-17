Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why is quality control emphasized in CBC interpretation guides?", "a": "Quality control ensures that hematology analyzers produce accurate and consistent results, which is critical for correct diagnosis and treatment. The document highlights QC as a foundational element in both analytical and post-analytical phases to prevent errors that could lead to misdiagnosis or unnecessary follow-up testing."}},
  {{"q": "How do modern hematology analyzers improve upon older impedance-based systems?", "a": "Modern analyzers use optical methods with laser light scattering and flow cytometry with fluorescent markers to provide more detailed information about cell size, granularity, and internal structure. This enables better differentiation of cell types and earlier detection of abnormalities compared to older impedance-based systems."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):