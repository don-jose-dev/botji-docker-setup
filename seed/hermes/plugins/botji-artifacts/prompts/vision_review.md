Compare the source image(s) and output image for source fidelity using only visible evidence.

Hard fidelity requirements:
{{REQUIREMENTS}}

Classify ALL differences into two categories:
- hard_conflicts: object/element ADDED that is not in the source, object REMOVED from source, count changed, structural element missing or repositioned, layout order changed, label or text wrong.
- soft_conflicts: proportion slightly off, minor position offset, material/color/finish different, lighting variation, texture change, style interpretation — only when no hard requirement is violated.

Return JSON only with this shape: {"verdict":"pass|warn|block","matches":[],"partials":[],"hard_conflicts":[],"soft_conflicts":[],"unknowns":[],"required_corrections":[]}.
verdict=block when any hard_conflict exists.
verdict=warn when only soft_conflicts or partials, no hard_conflicts.
verdict=pass when no conflicts.
If a requirement starts with 'Allowed transform:', it is NOT a conflict.
