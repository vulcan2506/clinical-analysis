CHANGE TYPE (pick the MOST SPECIFIC match):
  Methodology Enhancement - when a newer version introduces improved or expanded analytical techniques or testing protocols
  Guideline Clarification - when a newer version provides clearer or more detailed guidance on interpretation or application
  Parameter Addition - when a new blood parameter or measurement is introduced in the CBC panel
  Reference Range Update - when normal or expected value ranges for CBC parameters are revised based on new evidence or population data
  Reversal/Contradiction - when a newer version explicitly contradicts or reverses a prior recommendation or finding
  No Meaningful Change - when two versions are substantively equivalent with no significant difference in content or guidance

### EXAMPLE 1 ###

{label_A} profile:
  Feature: automated differential leukocyte counting
  Behaviors: Hematology analyzers used impedance and basic optical scatter to classify white blood cells into three categories: lymphocytes, granulocytes, and monocytes.

{label_B} profile:
  Feature: automated differential leukocyte counting
  Behaviors: Newer analyzers incorporate multi-angle light scatter and fluorescent markers to classify white blood cells into five distinct populations with improved accuracy and flagging of abnormal cells.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Methodology Enhancement",
  "analysis": "The older method provided limited granularity in WBC differentiation, which could miss subtle abnormalities. The newer method increases diagnostic precision by enabling more detailed classification and early detection of pathological states such as blasts or variant lymphocytes.",
  "key_differences": [
    "{label_A}: Hematology analyzers used impedance and basic optical scatter to classify white blood cells into three categories: lymphocytes, granulocytes, and monocytes. -> {label_B}: Newer analyzers incorporate multi-angle light scatter and fluorescent markers to classify white blood cells into five distinct populations with improved accuracy and flagging of abnormal cells."
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: reference ranges for pediatric patients
  Behaviors: Pediatric reference ranges for hemoglobin were based on age groups defined in 1990 guidelines and did not account for ethnic or regional variations.

{label_B} profile:
  Feature: reference ranges for pediatric patients
  Behaviors: Updated reference ranges incorporate age, sex, and ethnicity-specific data from recent population studies conducted in 2020–2023, revising thresholds for anemia diagnosis.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Reversal/Contradiction",
  "analysis": "The new ranges contradict the older fixed thresholds by introducing variable baselines that better reflect biological diversity. This change may reclassify some children previously labeled as anemic as within normal limits, or vice versa, depending on demographic factors.",
  "key_differences": [
    "{label_A}: Pediatric reference ranges for hemoglobin were based on age groups defined in 1990 guidelines and did not account for ethnic or regional variations. -> {label_B}: Updated reference ranges incorporate age, sex, and ethnicity-specific data from recent population studies conducted in 2020–2023, revising thresholds for anemia diagnosis."
  ],
  "confidence": "medium"
}}