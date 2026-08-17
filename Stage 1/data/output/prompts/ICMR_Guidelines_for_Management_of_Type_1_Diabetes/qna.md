Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the guideline change the recommended blood glucose targets for children with T1DM?", "a": "The change was driven by emerging evidence showing that tighter pre-meal glucose targets (90\u2013150 mg/dL) reduce hypoglycemia risk without increasing overall hyperglycemia burden in pediatric patients. The previous range (80\u2013180 mg/dL) was associated with higher rates of hypoglycemic events, particularly overnight."}},
  {{"q": "How should healthcare providers implement the new carbohydrate counting recommendations for insulin pump users?", "a": "Providers should ensure all insulin pump users receive standardized carbohydrate counting training, including use of integrated bolus calculators and real-time glucose monitoring data. The guideline now requires documentation of carbohydrate counting accuracy in pump settings to optimize mealtime insulin dosing."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):