CHANGE TYPE (pick the MOST SPECIFIC match):
  Recommendation Clarification - when a guideline recommendation is refined or made more specific without changing the core intent
  New Evidence Incorporation - when new clinical evidence or research findings are added to support or modify existing recommendations
  Risk Stratification Update - when the criteria for classifying patient risk categories are modified based on new data
  Treatment Modality Change - when a recommended treatment approach is explicitly reversed or contradicted in a subsequent version
  No Meaningful Change - when two versions of a guideline are compared and no substantive difference in recommendations or definitions is found

### EXAMPLE 1 ###

{label_A} profile:
  Feature: TSH suppression targets
  Behaviors: TSH suppression to below the reference range was recommended for all patients with intermediate-risk differentiated thyroid cancer.

{label_B} profile:
  Feature: TSH suppression targets
  Behaviors: Individualization of TSH suppression to below the reference range is now recommended based on patient-specific factors including age, comorbidities, and disease risk.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Recommendation Clarification",
  "analysis": "The earlier version applied a blanket recommendation for TSH suppression across all intermediate-risk patients. The updated guideline introduces nuance by emphasizing patient-specific factors, reflecting growing evidence that blanket suppression may not be optimal for all patients and could increase adverse effects in some populations.",
  "key_differences": [
    "{label_A}: TSH suppression to below the reference range was recommended for all patients with intermediate-risk differentiated thyroid cancer. -> {label_B}: Individualization of TSH suppression to below the reference range is now recommended based on patient-specific factors including age, comorbidities, and disease risk."
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: Completion thyroidectomy criteria
  Behaviors: Completion thyroidectomy was routinely recommended for all patients with papillary thyroid microcarcinoma found after initial lobectomy.

{label_B} profile:
  Feature: Completion thyroidectomy criteria
  Behaviors: Completion thyroidectomy for cancer following initial lobectomy may now be considered only in select cases with high-risk features, not routinely.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Treatment Modality Change",
  "analysis": "The earlier version recommended routine completion thyroidectomy for all microcarcinomas, reflecting historical practice patterns. The updated guideline reverses this stance, recommending a more selective approach based on risk stratification and patient-specific factors, aligning with evidence showing limited benefit for many low-risk patients.",
  "key_differences": [
    "{label_A}: Completion thyroidectomy was routinely recommended for all patients with papillary thyroid microcarcinoma found after initial lobectomy. -> {label_B}: Completion thyroidectomy for cancer following initial lobectomy may now be considered only in select cases with high-risk features, not routinely."
  ],
  "confidence": "medium"
}}