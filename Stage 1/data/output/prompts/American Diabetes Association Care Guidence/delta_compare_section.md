CHANGE TYPE (pick the MOST SPECIFIC match):
  Guideline Clarification - when existing recommendations are restated with more precise language or additional context without changing the core recommendation
  New Recommendation Addition - when a new clinical recommendation is introduced based on emerging evidence or clinical need
  Recommendation Strengthening - when existing recommendations are reinforced with stronger evidence or expanded scope
  Recommendation Reversal - when a previous recommendation is explicitly contradicted or reversed due to new evidence or safety concerns
  No Meaningful Change - when two versions address the same topic but the differences are editorial or insignificant to clinical practice

### EXAMPLE 1 ###

{label_A} profile:
  Feature: SGLT2 inhibitor use in heart failure
  Behaviors: SGLT2 inhibitors were recommended primarily for glycemic control in patients with type 2 diabetes and established cardiovascular disease.

{label_B} profile:
  Feature: SGLT2 inhibitor use in heart failure
  Behaviors: SGLT2 inhibitors are now recommended for patients with type 2 diabetes and heart failure with reduced ejection fraction regardless of glycemic control status.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "New Recommendation Addition",
  "analysis": "The 2026 update expands the indication for SGLT2 inhibitors beyond glycemic management to include heart failure treatment, reflecting new cardiovascular outcome evidence. This represents a significant expansion of therapeutic use with implications for cardiology and endocrinology practice.",
  "key_differences": [
    "{label_A}: SGLT2 inhibitors were recommended primarily for glycemic control in patients with type 2 diabetes and established cardiovascular disease. -> {label_B}: SGLT2 inhibitors are now recommended for patients with type 2 diabetes and heart failure with reduced ejection fraction regardless of glycemic control status."
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: A1C targets in older adults
  Behaviors: A1C targets for older adults with diabetes were generally set at <7.5% for healthy individuals and <8.5% for those with multiple comorbidities.

{label_B} profile:
  Feature: A1C targets in older adults
  Behaviors: A1C targets for older adults are now individualized based on patient-specific factors including life expectancy, cognitive function, and hypoglycemia risk, with no fixed numeric targets recommended.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Recommendation Reversal",
  "analysis": "The 2026 update reverses the previous fixed A1C targets for older adults, replacing them with individualized goals based on comprehensive geriatric assessment. This represents a fundamental shift from population-based to patient-centered care in this vulnerable population.",
  "key_differences": [
    "{label_A}: A1C targets for older adults with diabetes were generally set at <7.5% for healthy individuals and <8.5% for those with multiple comorbidities. -> {label_B}: A1C targets for older adults are now individualized based on patient-specific factors including life expectancy, cognitive function, and hypoglycemia risk, with no fixed numeric targets recommended."
  ],
  "confidence": "medium"
}}