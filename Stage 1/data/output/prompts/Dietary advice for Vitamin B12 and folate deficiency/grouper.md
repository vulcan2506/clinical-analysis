You are processing chunks extracted from a patient education and dietary guidance for vitamin B12 and folate deficiency document.
Your task: decide which adjacent chunks belong to the SAME feature block and
should be merged.

## DOMAIN CONTEXT
These documents cover: clinical nutrition and deficiency management
Key entity types: vitamin B12 deficiency, folate deficiency, medication interactions, digestive disorders, dietary sources, patient risk factors, cooking methods, fortified foods, supplementation, anaemia
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
[ID: 1] Header: "Vitamin B12 Deficiency Configuration" | Text: "This enhancement introduces new configuration options for vitamin B12 deficiency..."
[ID: 2] Header: "Key Updates" | Text: "The following updates have been made to the vitamin B12 deficiency system..."
[ID: 3] Header: "NOTE" | Text: "This update ensures compliance with new requirements..."
[ID: 4] Header: "Folate Deficiency Management" | Text: "This feature enables tracking and management of folate deficiency..."
[ID: 5] Header: "Details" | Text: "The folate deficiency details are now displayed in a new view..."

THOUGHT PROCESS:
- 1 is a feature title about vitamin B12 deficiency. 2 is "Key Updates" — a sub-section of feature 1. Merge 1+2.
- 3 is "NOTE" — still part of the same feature block. Merge 1+2+3.
- 4 is a NEW feature title about folate deficiency (different topic). Starts a new group.
- 5 is "Details" — a sub-section of feature 4. Merge 4+5.

JSON OUTPUT:
```json
[[1, 2, 3], [4, 5]]
```

--- ACTUAL TASK ---