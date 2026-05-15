# Source-Fidelity Review Prompt Template

Use after drafting an answer or generating an artifact.

```text
Review <reviewed_output_ref> against Prompt Contract <contract_id>.

Return JSON compatible with /opt/data/schemas/source_fidelity_review.schema.json.

Required axes, no omissions:
- source_coverage
- authority_alignment
- preserve_change
- groundedness
- uncertainty
- safety
- artifact_lineage
- actionability

For each axis, assign:
- compare_status: match | partial | missing | conflict | not_applicable
- severity: none | low | medium | high | blocking
- notes: concrete source-based reason

Blocking rules:
- hard constraint conflict
- unsupported fact stated as fact
- verified claimed without verification
- source authority reversed
- destructive/high-stakes action without approval
- preserve list silently changed
- silent model/provider/tool fallback
- generated artifact lacks lineage
- visual artifact not compared to source but called faithful

Final claim level:
- unverified: no review or source comparison
- draft: generated but not compared/validated
- reviewed: compared by Botji against available sources
- verified: actual verification command/schema/test/source check ran

If any axis is partial/missing/conflict, list required_corrections.
```
