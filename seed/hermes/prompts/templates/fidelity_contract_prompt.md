# Fidelity Prompt Contract Template

Two modes. Use the lowest that fits — do not gold-plate.

---

## QUICK (substantive, no artifacts, no high-stakes risk)

```
Contract: [intent in one line]
Mode: fidelity | grounded | creative
Sources: [list]
Preserve: [what must not change]
Change: [what is allowed]
Missing: [unknowns or "none"]
```

---

## FULL (artifacts, image transforms, high-stakes, multi-tool)

```
Contract: [contract_id]
Intent: [what the user wants]
Tier: substantive | high_stakes
Mode: fidelity | grounded | creative | verified

Sources:
- [id]: [type] · [authority: hard|soft] · [path or citation]

Authority: user instruction > uploaded files > project rules > memory > inference

Preserve:
- [exact facts, numbers, names, layout, constraints]

Change:
- [allowed transforms or generated output]

Missing:
- [unknowns that limit fidelity, or "none"]

Execution:
1. Pre-check for conflicts and missing inputs
2. [step 2]
3. [step 3]

Lineage:
- parent: [source artifact id/path or null]
- output: [planned output path/id]
- version: [Render v1 | Draft v2 | etc.]
- reason: [why this transform exists]

Approval required: yes | no
Risk: [specific risk or "none"]
```

---

## Artifact fidelity addendum (add when source file is involved)

```
Source artifacts: [ids]
Schema evidence: [ids]
Route: exact_copy | edit_image | render_schema | concept_generation
Forbidden routes: [e.g. image_generate]
Hard requirements: [schema constraints that must pass review]
Advisory: [style/finish preferences that can warn but not block]
```
