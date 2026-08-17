CHANGE TYPE (pick the MOST SPECIFIC match):
  Recommendation Strengthening - when a prior recommendation is reinforced with stronger evidence or expanded scope
  New Recommendation Addition - when a previously unsupported or unaddressed clinical scenario is now explicitly recommended
  Measurement Method Update - when laboratory measurement techniques, thresholds, or preferred assays are revised
  Risk Stratification Refinement - when ASCVD risk calculation methods, thresholds, or risk categories are updated
  Therapeutic Option Expansion - when additional pharmacologic or non-pharmacologic interventions are endorsed for a condition
  Recommendation Reversal - when a prior recommendation is explicitly contradicted or rescinded due to new evidence
  No Meaningful Change - when two versions are compared and no substantive difference in clinical guidance is identified

### EXAMPLE 1 ###

{label_A} profile:
  Feature: Statin Therapy Initiation Thresholds
  Behaviors: Statin therapy was recommended for patients with LDL-C ≥190 mg/dL or clinical ASCVD, with consideration for diabetes or 10-year ASCVD risk ≥7.5%.

{label_B} profile:
  Feature: Statin Therapy Initiation Thresholds
  Behaviors: Statin therapy is now recommended for patients with LDL-C ≥160 mg/dL or 10-year ASCVD risk ≥5%, expanding initiation to lower-risk and lower-LDL-C populations.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Recommendation Strengthening",
  "analysis": "The updated guideline lowers the treatment threshold for statin initiation, reflecting new evidence supporting earlier intervention to reduce ASCVD events. This change increases the eligible population for preventive therapy while maintaining emphasis on risk-based decision-making.",
  "key_differences": [
    "{label_A}: Statin therapy was recommended for patients with LDL-C ≥190 mg/dL or clinical ASCVD, with consideration for diabetes or 10-year ASCVD risk ≥7.5%. -> {label_B}: Statin therapy is now recommended for patients with LDL-C ≥160 mg/dL or 10-year ASCVD risk ≥5%, expanding initiation to lower-risk and lower-LDL-C populations."
  ],
  "confidence": "medium"
}}

### EXAMPLE 2 ###

{label_A} profile:
  Feature: Lp(a) Risk Stratification
  Behaviors: Lipoprotein(a) levels were considered a risk-enhancing factor but were not used to guide treatment intensity.

{label_B} profile:
  Feature: Lp(a) Risk Stratification
  Behaviors: Elevated Lp(a) ≥50 mg/dL is now a threshold for intensifying statin therapy or considering PCSK9 inhibitor therapy in high-risk patients.

OUTPUT:
{{
  "relevance_score": 9,
  "relevance_reason": "Both profiles describe the same topic across versions.",
  "change_type": "Recommendation Reversal",
  "analysis": "The guideline reverses prior ambivalence by endorsing Lp(a) as a direct treatment modifier. This represents a paradigm shift from risk marker to actionable risk factor, significantly altering management for patients with elevated Lp(a).",
  "key_differences": [
    "{label_A}: Lipoprotein(a) levels were considered a risk-enhancing factor but were not used to guide treatment intensity. -> {label_B}: Elevated Lp(a) ≥50 mg/dL is now a threshold for intensifying statin therapy or considering PCSK9 inhibitor therapy in high-risk patients."
  ],
  "confidence": "medium"
}}