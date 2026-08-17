CHANGE TYPE (pick the MOST SPECIFIC match):
  Therapeutic Option Addition - when a new treatment modality or drug class is introduced as an option
  Target Revision - when glycemic or clinical targets are updated to be more or less stringent
  Screening Protocol Expansion - when additional screening tests or populations are recommended
  Contradictory Recommendation - when a previous recommendation is explicitly reversed or contradicted
  No Meaningful Change - when two versions are substantively equivalent with no material differences

### EXAMPLE 1 ###

{label_A} profile:
  Feature: insulin initiation timing
  Behaviors: Insulin should be initiated only after failure of maximum oral therapy in type 2 diabetes patients

{label_B} profile:
  Feature: insulin initiation timing
  Behaviors: Consider early insulin initiation in patients with marked hyperglycemia at diagnosis to achieve rapid glycemic control

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Therapeutic Option Addition",
  "analysis": "Version A reflects a stepwise approach requiring oral therapy failure before insulin. Version B introduces early insulin use in select patients, expanding therapeutic options beyond the previous stepwise paradigm. This represents a significant shift toward more aggressive early management in appropriate cases.",
  "key_differences": [
    "{label_A}: Insulin should be initiated only after failure of maximum oral therapy in type 2 diabetes patients -> {label_B}: Consider early insulin initiation in patients with marked hyperglycemia at diagnosis to achieve rapid glycemic control"
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: glycemic targets in elderly
  Behaviors: Tight glycemic control is recommended for all patients regardless of age

{label_B} profile:
  Feature: glycemic targets in elderly
  Behaviors: Relaxed glycemic targets are advised for elderly patients with comorbidities to prevent hypoglycemia

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Contradictory Recommendation",
  "analysis": "Version A mandates tight control universally. Version B explicitly reverses this by recommending individualized, less stringent targets for elderly patients. This represents a fundamental contradiction in approach that requires careful patient stratification.",
  "key_differences": [
    "{label_A}: Tight glycemic control is recommended for all patients regardless of age -> {label_B}: Relaxed glycemic targets are advised for elderly patients with comorbidities to prevent hypoglycemia"
  ],
  "confidence": "medium"
}}