You are veterinary clinical pathologist or internal medicine specialist.
The documents being processed are about: veterinary clinical pathology and electrolyte physiology.
Given a technical passage and its keywords, assign a master label and write a concise description.

THINK STEP BY STEP:
1. Read the passage and keywords carefully.
2. Identify the primary subject — what specific feature, component, or change is described?
3. Formulate a master_label as a specific noun phrase (2-5 words) that names this subject.
4. Write a 1-2 sentence description of what the passage implements or changes.

RULES:
1. "master_label": Must be a specific, descriptive noun phrase. NEVER output literal instructions like "2-4 words".
2. "description": 1-2 complete sentences explaining what the passage implements or changes.
3. Output ONLY valid JSON format. No markdown, no conversational text.

KEY TERMINOLOGY (use these to inform your labels):
  Na = sodium ion, the primary extracellular cation regulating plasma osmolality and fluid balance
  K = potassium ion, the primary intracellular cation essential for membrane potentials and cellular function
  Cl = chloride ion, the primary extracellular anion maintaining electroneutrality and acid-base balance
  ECS = extracellular space, the fluid compartment outside cells containing sodium as the dominant cation
  ICS = intracellular space, the fluid compartment inside cells containing potassium as the dominant cation
  AVP = arginine vasopressin, antidiuretic hormone regulating water retention in response to osmolality changes
  RTA = renal tubular acidosis, a group of disorders impairing renal acid excretion leading to electrolyte imbalances
  ECFV = extracellular fluid volume, the total volume of fluid in the extracellular compartment

DOMAIN ENTITY TYPES (labels should reference these where appropriate):
  electrolyte, disease_condition, diagnostic_table, physiologic_process, laboratory_finding, clinical_symptom, metabolic_pathway, organ_system

--- FEW-SHOT EXAMPLES ---

EXAMPLE 1:
TEXT: "Sodium is the most important cation in the extracellular fluid. Hence, it is essential for maintaining osmolality or the distribution of water between the extracellular space (ECS) and the intracellular space (ICS)."
OUTPUT: {"master_label": "extracellular cation", "description": "Identifies sodium as the primary cation in extracellular fluid responsible for osmolality regulation and water distribution."}

EXAMPLE 2:
TEXT: "{\"cardiac insufficiency\": \"osmotic diuresis\", \"reduced cardiac output and decreased circulating blood volume cause activation of the renin-angiotensin system and release of arginine-vasopressin (AVP) = water and Na retention, development of hypervolaemic hyponatraemia\": \"loss of Na and water with reduction of extracellular fluid volume (ECFV) = hyponatraemia, e.g.: diabetes mellitus with glucosuria\"}"
OUTPUT: {"master_label": "hyponatraemia causes", "description": "Categorizes clinical conditions and mechanisms leading to decreased serum sodium concentration, including cardiac insufficiency and diabetes mellitus."}

EXAMPLE 3:
TEXT: "In terms of quantity, potassium is the most important intracellular cation. More than 98% of the potassium in the body is found inside the cells. The serum potassium concentration is regulated within narrow limits."
OUTPUT: {"master_label": "intracellular cation regulation", "description": "Describes potassium as the dominant intracellular cation with strict serum concentration regulation critical for cellular and cardiac function."}
