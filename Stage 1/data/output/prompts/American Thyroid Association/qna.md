Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the guideline change from recommending routine completion thyroidectomy to selective consideration?", "a": "The change reflects emerging evidence that routine completion thyroidectomy in patients with papillary thyroid microcarcinoma found after initial lobectomy provides limited benefit for many low-risk patients while increasing the risk of complications. The updated guideline emphasizes individualized decision-making based on specific high-risk features rather than a one-size-fits-all approach."}},
  {{"q": "How should clinicians now approach TSH suppression in intermediate-risk thyroid cancer patients?", "a": "The guideline now recommends individualizing TSH suppression targets based on patient-specific factors such as age, comorbidities, and disease risk rather than applying a blanket recommendation. This approach aims to balance the potential benefits of suppression against the risks of overtreatment, particularly in older patients or those with significant cardiovascular disease."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):