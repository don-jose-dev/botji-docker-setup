# Source-Fidelity Review Prompt Template

Use after drafting an answer or generating an artifact.

---

## Compact review (substantive tier — default)

```
✅ reviewed · coverage:[status] · ground:[status] · safety:[status] · lineage:[status]
```

Use `match` | `partial` | `missing` | `conflict` | `not_applicable` for each.
Claim level: `draft` | `reviewed` | `verified`

---

## Full review (high-stakes, artifacts, or when user requests it)

```
Review [reviewed_output_ref] against Contract [contract_id].

Axes (all required, no omissions):

| Axis              | Status  | Notes |
|-------------------|---------|-------|
| source_coverage   |         |       |
| authority_alignment|        |       |
| preserve_change   |         |       |
| groundedness      |         |       |
| uncertainty       |         |       |
| safety            |         |       |
| artifact_lineage  |         |       |
| actionability     |         |       |

Status values: match | partial | missing | conflict | not_applicable
Severity: none | low | medium | high | blocking

Final claim level:
- unverified — no review or source comparison
- draft — generated but not compared/validated
- reviewed — compared by Botji against available sources
- verified — actual verification command/schema/test/source check ran
```

---

## Blocking rules — stop before final send if ANY are true

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

If any axis is partial/missing/conflict, list `required_corrections` before sending.
