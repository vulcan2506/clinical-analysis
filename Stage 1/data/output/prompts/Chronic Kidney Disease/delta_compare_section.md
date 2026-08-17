CHANGE TYPE (pick the MOST SPECIFIC match):
  Guideline Clarification - when the newer version provides additional detail or precision to existing recommendations without changing the core intent
  New Recommendation - when the newer version introduces a new clinical recommendation or expands scope to a previously unaddressed patient group
  Risk Stratification Update - when the newer version updates risk categories, thresholds, or prediction equations used for clinical decision-making
  Contradictory Recommendation - when the newer version explicitly reverses or contradicts a prior recommendation in a clinically meaningful way
  No Meaningful Change - when the two versions are substantively equivalent with no material difference in clinical guidance

### EXAMPLE 1 ###

{label_A} profile:
  Feature: use of SGLT2 inhibitors in CKD
  Behaviors: SGLT2 inhibitors were recommended for patients with type 2 diabetes and CKD with albuminuria (ACR ≥30 mg/g) and eGFR ≥30 mL/min/1.73m².

{label_B} profile:
  Feature: use of SGLT2 inhibitors in CKD
  Behaviors: SGLT2 inhibitors are now recommended for patients with CKD with or without diabetes who have albuminuria (ACR ≥30 mg/g) and eGFR ≥20 mL/min/1.73m², expanding eligibility to lower eGFR ranges.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "New Recommendation",
  "analysis": "The 2024 guideline broadens the indication for SGLT2 inhibitors to include non-diabetic CKD patients and lowers the eGFR threshold, reflecting new evidence of benefit across a wider CKD population. This represents an expansion of therapeutic options rather than a reversal of prior guidance.",
  "key_differences": [
    "{label_A}: SGLT2 inhibitors were recommended for patients with type 2 diabetes and CKD with albuminuria (ACR ≥30 mg/g) and eGFR ≥30 mL/min/1.73m². -> {label_B}: SGLT2 inhibitors are now recommended for patients with CKD with or without diabetes who have albuminuria (ACR ≥30 mg/g) and eGFR ≥20 mL/min/1.73m², expanding eligibility to lower eGFR ranges."
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: blood pressure targets in CKD with diabetes
  Behaviors: Target systolic blood pressure <130 mmHg was recommended for adults with CKD and diabetes.

{label_B} profile:
  Feature: blood pressure targets in CKD with diabetes
  Behaviors: Target systolic blood pressure <140 mmHg is now recommended for adults with CKD and diabetes, with <130 mmHg reserved for those at higher cardiovascular risk.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Contradictory Recommendation",
  "analysis": "The updated guideline explicitly relaxes the blood pressure target for CKD patients with diabetes from <130 to <140 mmHg, reversing a prior more aggressive target. This change reflects evolving evidence on the balance of benefits and harms at lower blood pressure levels.",
  "key_differences": [
    "{label_A}: Target systolic blood pressure <130 mmHg was recommended for adults with CKD and diabetes. -> {label_B}: Target systolic blood pressure <140 mmHg is now recommended for adults with CKD and diabetes, with <130 mmHg reserved for those at higher cardiovascular risk."
  ],
  "confidence": "medium"
}}