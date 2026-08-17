Read this {doc_purpose} text about "{topic}" and generate {n} Q&A pairs that explore WHY this is the case, HOW the mechanism or rule works, or WHAT the implications are for real-world use.

Rules:
- Ask "why", "how", or "what does this imply" — avoid pure lookup questions.
- Answers: 2-3 sentences with brief reasoning before the conclusion.
- If the text is too short for {n} questions, generate fewer — no padding.
- Output ONLY a JSON array — nothing before or after it. Close all brackets.

Examples of the depth we want:
[
  {{"q": "Why did the guideline lower the LDL-C threshold for statin initiation from 190 mg/dL to 160 mg/dL?", "a": "New randomized trial data and meta-analyses demonstrated that initiating statin therapy at lower LDL-C levels in intermediate-risk patients significantly reduces major adverse cardiovascular events, supporting a more aggressive preventive approach."}},
  {{"q": "How should clinicians incorporate Lp(a) testing into routine practice given the updated recommendations?", "a": "Clinicians should consider Lp(a) testing in patients with a family history of premature ASCVD, personal history of ASCVD despite low LDL-C, or intermediate risk scores. Elevated Lp(a) \u226550 mg/dL should prompt intensification of lipid-lowering therapy or referral to a lipid specialist."}}
]

TOPIC: {topic}
TEXT:
{text}

OUTPUT (JSON array only, max {n} pairs, close all brackets):