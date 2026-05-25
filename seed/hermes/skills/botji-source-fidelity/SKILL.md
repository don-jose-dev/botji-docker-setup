---
name: botji-source-fidelity
description: Source-fidelity review for every substantive or high-stakes response — 8-axis comparison against the Prompt Contract with blocking rules and compact review badge.
tags:
  - botji
  - fidelity
  - review
  - artifact-fidelity
---

# Botji Source-Fidelity Review Skill

Use this skill before every substantive or high-stakes response.

## Goal

Compare the generated answer or artifact against the Prompt Contract. Do not review vibes. Review deltas.

Fidelity means the output preserves the source's intent, constraints, facts, numbers, names, structure, and boundaries. Style improvements are allowed only when the contract allows them.

## Research basis

This rubric is aligned with public evaluation and provenance guidance:

- Microsoft RAG evaluators define groundedness as how well a generated response aligns with the given context without fabricating content: https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators
- W3C PROV defines provenance as information about entities, activities, and people involved in producing data or things, useful for judging quality, reliability, and trustworthiness: https://www.w3.org/TR/prov-overview/
- NIST AI RMF emphasizes trustworthy AI risk management practices and measurement/management discipline: https://www.nist.gov/itl/ai-risk-management-framework
- OWASP GenAI/LLM guidance emphasizes identifying, mitigating, and documenting security and safety risks in LLM applications: https://owasp.org/www-project-top-10-for-large-language-model-applications/

## Required axes

Every substantive or high-stakes final reply must include every axis below. Omitting an axis is a review failure.

- source_coverage
- authority_alignment
- preserve_change
- groundedness
- uncertainty
- safety
- artifact_lineage
- actionability

## compare_status values

- match: output follows the contract
- partial: output partly follows but has gaps
- missing: required element absent
- conflict: output contradicts a hard source/constraint
- not_applicable: axis does not apply

## severity values

- none: no issue
- low: minor gap or disclosed limitation
- medium: meaningful limitation; user can still use output with caution
- high: serious fidelity risk; needs correction or user approval
- blocking: do not send as final without correction

## Blocking rules

Block before final send if ANY of these are true:

| # | Condition |
|---|---|
| 1 | Hard constraint violated |
| 2 | Unsupported claim stated as fact |
| 3 | `verified` claimed without an actual verification step |
| 4 | Source authority reversed |
| 5 | Destructive/high-stakes action taken without approval |
| 6 | Preserve list changed silently |
| 7 | Provider/model/tool fallback happened silently |
| 8 | Artifact lineage missing for a generated artifact |
| 9 | Generated artifact called faithful without a comparison step |
| 10 | Required review axis omitted |
| 11 | Upper/lower layout zone boundaries misaligned without user confirmation |
| 12 | Required route (image_edit, exact_copy) failed and was replaced by text-to-image, even if disclosed |

## Persistence rule

For tool-backed work or generated artifacts, persist a review JSON when file tools are available:

- Path: `/opt/data/reviews/<review_id>.json`
- Validate with `botji-validate-review /opt/data/reviews/<review_id>.json` when practical.
- If the review cannot be persisted or validated, disclose that and set `final_claim_level` no higher than `reviewed`.
- Never claim a persisted review exists unless the file was actually written.

## Pipeline

The procedural loop (`source_register` → `evidence_extract` → `operation_run` → `output_write` → `review_record`) is owned by `botji-artifact-fidelity` — see that skill for the full step list. This skill owns only the **review contract**: which axes are required, what each `compare_status` means, which conditions block delivery, how the receipt JSON is shaped, and the persisted-review rule above.

## Review JSON

Use this shape internally or with `botji-codex-review`. Include all eight axes.
For visual artifacts, add `visual_route` and `source_geometry` axes when practical.

```json
{
  "review_id": "review-YYYYMMDDTHHMMSSZ",
  "contract_id": "prompt-YYYYMMDDTHHMMSSZ",
  "reviewed_output_ref": "answer-or-artifact-v1",
  "verdict": "pass",
  "final_claim_level": "reviewed",
  "axes": [
    {
      "axis": "source_coverage",
      "compare_status": "match",
      "severity": "none",
      "notes": "All required sources from the contract were used."
    },
    {
      "axis": "authority_alignment",
      "compare_status": "match",
      "severity": "none",
      "notes": "Hard constraints outrank assumptions and style choices."
    },
    {
      "axis": "preserve_change",
      "compare_status": "match",
      "severity": "none",
      "notes": "Preserve list stayed intact; changes are within the change list."
    },
    {
      "axis": "groundedness",
      "compare_status": "match",
      "severity": "none",
      "notes": "Claims are grounded in cited sources/tool outputs or labeled assumptions."
    },
    {
      "axis": "uncertainty",
      "compare_status": "match",
      "severity": "none",
      "notes": "Missing inputs and assumptions are explicit."
    },
    {
      "axis": "safety",
      "compare_status": "match",
      "severity": "none",
      "notes": "No unsafe, destructive, or secret-bearing action was taken."
    },
    {
      "axis": "artifact_lineage",
      "compare_status": "match",
      "severity": "none",
      "notes": "Parent artifact, prompt/version, output artifact, and review are linked."
    },
    {
      "axis": "actionability",
      "compare_status": "match",
      "severity": "none",
      "notes": "The user has clear next steps or a usable artifact."
    }
  ],
  "blockers": [],
  "required_corrections": [],
  "verification_steps": [],
  "reviewer_notes": ""
}
```

Schema:
`/opt/data/schemas/source_fidelity_review.schema.json`

Validator:
`botji-validate-review /path/to/review.json`

## Compact footer template

Review:
- source_coverage: match|partial|missing|conflict|not_applicable
- authority_alignment: match|partial|missing|conflict|not_applicable
- preserve_change: match|partial|missing|conflict|not_applicable
- groundedness: match|partial|missing|conflict|not_applicable
- uncertainty: match|partial|missing|conflict|not_applicable
- safety: match|partial|missing|conflict|not_applicable
- artifact_lineage: match|partial|missing|conflict|not_applicable
- actionability: match|partial|missing|conflict|not_applicable
- final_claim_level: unverified|draft|reviewed|verified
- verification: method or “not verified”

## Quality gates

Before final send, ask:

1. Did I include all eight axes?
2. Did I disclose every failed or skipped tool/source?
3. Did I separate facts from assumptions?
4. For generated artifacts, did I record parent → prompt → output → review lineage?
5. For visual artifacts, did I compare the output back to the source?
6. Is `verified` supported by an actual verification step?
7. For cabinetry or multi-zone layout artifacts: do upper-zone and lower-zone vertical module boundaries align? If they differ, did I ask the user whether this is intentional before passing the review?
8. If the Prompt Contract required image_edit or exact_copy and the route failed, did I set verdict to block rather than pass?

If any answer is no, correct the response or lower the claim level before final send.
