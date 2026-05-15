# Fidelity Prompt Contract Template

Use for every tool-backed or artifact-generating task.

```text
Build Prompt Contract <contract_id>.

Intent:
<what the user wants>

Tier:
casual | substantive | high_stakes

Truth mode:
creative | grounded | fidelity | verified

Sources:
- <source_id>: <source_type>, <authority>, <path/citation>, <summary>

Authority order:
1. Current explicit user instruction
2. Uploaded/current files and command outputs
3. Project rules/schemas/tests
4. Durable memory
5. External web/source material
6. Inference and style assumptions

Preserve:
- <facts, numbers, names, layout, constraints that must stay true>

Change:
- <allowed transformations or generated output>

Missing inputs:
- <unknowns that limit fidelity>

Execution plan:
1. Pre-review contract for contradictions and missing inputs.
2. Execute only inside preserve/change boundaries.
3. Persist output artifact path/id if generated.
4. Run source-fidelity review with all eight axes.

Artifact lineage:
parent_artifact_id: <input path/id or null>
output_artifact_id: <planned output path/id or null>
version_label: Prompt v1
change_reason: <why this transformation exists>

Final requirement:
The final answer must include all eight review axes and final_claim_level.
```
