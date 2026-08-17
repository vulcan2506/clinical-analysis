Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why does cardiac insufficiency lead to hyponatraemia in some patients but hypernatraemia in others?", "a": "Cardiac insufficiency can cause hyponatraemia through activation of the renin-angiotensin-aldosterone system and AVP release leading to water retention, while hypernatraemia may result from aggressive diuretic therapy causing sodium retention or fluid restriction protocols that concentrate sodium despite total body sodium depletion."}},
  {{"q": "How does the intracellular distribution of potassium affect serum potassium measurements in clinical practice?", "a": "Serum potassium reflects only a small fraction of total body potassium, primarily the extracellular pool. Shifts between intracellular and extracellular compartments due to acid-base disturbances, insulin therapy, or catecholamine effects can cause significant changes in serum levels without altering total body potassium, necessitating careful clinical correlation."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):