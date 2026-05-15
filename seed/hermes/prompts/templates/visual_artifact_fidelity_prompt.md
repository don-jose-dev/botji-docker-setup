# Visual Artifact Fidelity Prompt Template

Use for image-to-image, image-to-render, CAD-to-render, screenshot-to-mockup, diagram, slide, and design tasks.

```text
Visual Fidelity Task

Parent artifact:
<path/id>

User intent:
<requested transformation>

Source facts to preserve:
- Layout/order:
- Object count:
- Positions:
- Dimensions/text/labels:
- Required components:
- Forbidden changes:

Allowed assumptions:
- Materials:
- Colors:
- Lighting:
- Camera/view:
- Decor/environment:

Generation prompt:
Create <output type> that preserves the source facts above. Any missing materials, colors, lighting, camera, and decor are assumptions, not source facts. Do not alter preserved layout, counts, labels, or visible dimensions.

Output artifact:
<path/id/version>

Post-generation comparison:
- Match:
- Partial:
- Conflict:
- Assumptions:
- Not checked:

Final claim level:
draft | reviewed | verified
```
