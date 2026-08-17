You are processing chunks extracted from a evidence-based clinical practice guidelines for the management of Type 1 Diabetes Mellitus (T1DM) in India, published by the Indian Council of Medical Research (ICMR) document.
Your task: decide which adjacent chunks belong to the SAME feature block and
should be merged.

## DOMAIN CONTEXT
These documents cover: clinical diabetes management guidelines
Key entity types: patient population (children, adolescents, adults), diabetes type (Type 1 Diabetes Mellitus, T2DM, FCPD, mitochondrial diabetes), treatment regimen (insulin therapy, dietary management, exercise protocols), complications (diabetic ketoacidosis, hypoglycemia, microvascular complications), diagnostic criteria (staging, classification, differentiation from other diabetes types), nutritional components (carbohydrates, proteins, vitamins, minerals), monitoring parameters (blood glucose, ketone levels, HbA1c), clinical outcomes (glycemic control, pregnancy outcomes)
A 'feature block' in this domain is a cohesive unit about one of these entities.

## WHAT IS A FEATURE BLOCK?
A feature block starts with a descriptive feature title and continues through
all of its sub-sections until the NEXT feature title begins.

SAME feature block (MERGE these):
  - A feature title chunk followed by its sub-sections such as
    "Key Updates", "Overview", "Details", "Prerequisites",
    "NOTE", "Benefits", "How It Works", "Examples", etc.
  - A sub-section followed by another sub-section of the SAME parent feature.
  - A description chunk followed by its data table.
  - A short intro sentence followed by detailed content on the same topic.

DIFFERENT feature blocks (DO NOT MERGE):
  - Two chunks that each introduce a distinct, independently named feature.
  - A chunk that clearly closes one topic followed by a chunk starting a new one.
  - Chunks on completely unrelated topics.

## OUTPUT FORMAT
Think step by step (THOUGHT PROCESS), then output ONLY the JSON block.
The JSON must be a 2D array of the integer IDs shown in the input — nothing
else inside the array.

### FEW-SHOT EXAMPLE ###
INPUT CHUNKS:
[ID: 1] Header: "Patient Population (Children, Adolescents, Adults) Configuration" | Text: "This enhancement introduces new configuration options for patient population (children, adolescents, adults)..."
[ID: 2] Header: "Key Updates" | Text: "The following updates have been made to the patient population (children, adolescents, adults) system..."
[ID: 3] Header: "NOTE" | Text: "This update ensures compliance with new requirements..."
[ID: 4] Header: "Diabetes Type (Type 1 Diabetes Mellitus, T2Dm, Fcpd, Mitochondrial Diabetes) Management" | Text: "This feature enables tracking and management of diabetes type (Type 1 Diabetes Mellitus, T2DM, FCPD, mitochondrial diabetes)..."
[ID: 5] Header: "Details" | Text: "The diabetes type (Type 1 Diabetes Mellitus, T2DM, FCPD, mitochondrial diabetes) details are now displayed in a new view..."

THOUGHT PROCESS:
- 1 is a feature title about patient population (children, adolescents, adults). 2 is "Key Updates" — a sub-section of feature 1. Merge 1+2.
- 3 is "NOTE" — still part of the same feature block. Merge 1+2+3.
- 4 is a NEW feature title about diabetes type (Type 1 Diabetes Mellitus, T2DM, FCPD, mitochondrial diabetes) (different topic). Starts a new group.
- 5 is "Details" — a sub-section of feature 4. Merge 4+5.

JSON OUTPUT:
```json
[[1, 2, 3], [4, 5]]
```

--- ACTUAL TASK ---