---
name: botji-prompt-contract
description: Structured Prompt Contract for all substantive and high-stakes work — quick (6-field) or full (13-field) format with authority order, preserve/change lists, and artifact lineage.
tags:
  - botji
  - contract
  - fidelity
---

# Botji Prompt Contract Skill

Use this skill whenever the user's message is non-trivial.

## Trigger

Use for:
- architecture
- product decisions
- Docker/deployment
- coding
- document/artifact generation
- research
- prompt engineering
- planning
- domain-specific work
- anything with constraints, sources, or risk

Do not use for:
- greetings
- tiny factual definitions
- casual banter
- simple rewrites with no risk

## Procedure

1. Classify tier:
   - casual
   - substantive
   - high_stakes

2. Select truth mode:
   - creative
   - grounded
   - fidelity
   - verified

   Defaults:
   - Use `fidelity` for transformations of user-provided files, images, layouts, quotes, code, specifications, or prior artifacts.
   - Use `grounded` for general analysis from supplied context.
   - Use `creative` only when the user asks for ideation/style invention and no source facts are being preserved.
   - Use `verified` only after an actual verification step.

3. Extract sources:
   - explicit_user_request
   - uploaded_file
   - project_file
   - memory
   - external_web
   - tool_output
   - inference

4. Assign authority:
   - hard_constraint
   - soft_preference
   - context
   - hypothesis

5. Build preserve/change lists:
   - Preserve means “must stay true.”
   - Change means “allowed to modify, improve, or create.”
   - Numbers, labels, names, positions, file paths, dimensions, APIs, and user-stated boundaries usually belong in preserve.
   - Style, formatting, camera angle, wording, and implementation details belong in change only if the user allowed them.

6. Pre-review:
   - missing inputs
   - contradictions
   - ambiguous authority
   - high-stakes actions
   - unsupported claims
   - required verification
   - expected artifact lineage

7. Execute:
   - For substantive work, continue only inside the contract.
   - For high-stakes work, stop at approval unless explicitly approved.
   - If a selected provider/model/tool changes or fails, do not silently switch; update the contract or ask.

8. Final reply:
   - Include compact contract.
   - Include visible step list.
   - Include answer.
   - Include source-fidelity review footer with all required axes.

## Persistence rule

For tool-backed work or artifact generation, persist the contract when file tools are available:

- Path: `/opt/data/prompts/<contract_id>.json`
- Use the schema at `/opt/data/schemas/prompt_contract.schema.json` when practical.
- If the contract cannot be persisted, state that in the final review and mark `artifact_lineage` no higher than `partial`.
- Never claim a persisted contract exists unless the file was actually written.

## Artifact fidelity contract addendum

For source-bound files, images, renders, diagrams, CAD, screenshots, slides, documents, or design transformations, the Prompt Contract must include `artifact_fidelity`:

- source artifact ids and paths
- normalized schema evidence ids when available
- extracted source constraints: object counts, positions, dimensions/text, labels, layout relationships, line spans, page/table structure, or DXF layers/entities
- allowed assumptions and advisory preferences
- hard requirements that must not drift
- selected route: `exact_copy`, `render_schema`, `edit_image`, `manual_review`, or `ask_user`
- forbidden routes: usually include `prompt_only_generation` for source-bound work
- comparator requirement and current comparator status
- review requirement before final reply

If the user provides a source file, default the route to `exact_copy` until the user explicitly authorizes a transform route and change list. If the user says “make 3D” after an image upload, default to `fidelity`: preserve layout and dimensions as far as visible; treat materials/color/camera as assumptions unless specified.
For source-image fidelity work, `image_generate` is prompt-only and must not be selected unless the contract explicitly marks `visual_mode: concept_generation`.

## Premium-brief discipline (route=edit_image only)

When the contract selects route `edit_image` (and `fidelity_mode != "exact_copy"`), the brief passed as `instructions=` MUST follow `botji-premium-brief`. The contract is invalid until the brief passes the **noise-vocabulary check** and contains all four required specifications.

**Banned noise vocabulary in the brief** (reject the contract if any appears):

- `realistic`, `photorealistic`, `hyperrealistic`
- `high quality`, `8K`, `4K`, `ultra HD`, `HDR`
- `beautiful`, `nice`, `gorgeous`, `stunning`, `amazing`
- `modern style`, `luxury`, `elegant`, `polished`, `refined`, `sleek`, `sophisticated`
- `good lighting`, `warm tones`, `well-lit`
- `cosy`, `inviting`, `dreamy`, `magical`

**Required specifications** (reject the contract if any is missing):

1. **LIGHT** — at least one explicit Kelvin temperature (e.g. `2700K`, `3000K`, `4200K`)
2. **MATERIALS** — at least three named materials with finish (e.g. `rift-sawn white oak with hand-rubbed oil`, `honed Carrara marble with grey veining`)
3. **REFERENCE** — at least one specific genre or publication anchor (e.g. `Dezeen editorial`, `AD residential`, `Norm Architects residential`, `Apple Studio product`)
4. **SIGNATURE** — exactly one signature detail described as a quality of light or surface (not an object)

If any required specification is missing, fix the brief before persisting the contract. If any banned noise word is present, rewrite that section with specifics from the `botji-premium-brief` vocabulary tables.

## Contract skeleton

```json
{
  "contract_id": "prompt-YYYYMMDDTHHMMSSZ",
  "version": "Prompt v1",
  "created_at": "YYYY-MM-DDTHH:MM:SSZ",
  "tier": "substantive",
  "user_intent": "",
  "truth_mode": "fidelity",
  "sources": [
    {
      "source_id": "user-msg-1",
      "source_type": "explicit_user_request",
      "authority": "hard_constraint",
      "summary": "",
      "path_or_citation": null
    }
  ],
  "authority_order": [],
  "preserve": [],
  "change": [],
  "execution_plan": [
    {
      "step_id": "1",
      "actor": "hermes",
      "visible": true,
      "action": "Build and pre-review Prompt Contract."
    }
  ],
  "missing_inputs": [],
  "risk_level": "medium",
  "approval_required": false,
  "artifact_lineage": {
    "parent_artifact_id": null,
    "output_artifact_id": null,
    "version_label": "Prompt v1",
    "change_reason": ""
  },
  "artifact_fidelity": {
    "source_artifact_ids": [],
    "normalized_schema_ids": [],
    "selected_route": "exact_copy",
    "forbidden_routes": ["prompt_only_generation"],
    "hard_requirements": ["Preserve source artifacts byte-for-byte unless the user explicitly authorizes a transform route."],
    "advisory_preferences": [],
    "comparator_required": false,
    "comparator_status": "not_applicable",
    "review_required": true
  },
  "visual_fidelity": {
    "visual_mode": "none",
    "source_image_paths": [],
    "selected_route": "manual_review",
    "forbidden_routes": [],
    "geometry_authority": "not_applicable",
    "review_required": false
  },
  "notes": ""
}
```

Schema:
`/opt/data/schemas/prompt_contract.schema.json`

Helper:
`botji-contract-new`

## Compact visible contract template

Prompt Contract:
- Intent:
- Truth mode:
- Hard constraints:
- Sources:
- Preserve:
- Change:
- Missing:
- Risk:
- Lineage:
