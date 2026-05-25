---
name: botji-cad-elevation
description: Production-drawing skill for CAD / DXF / DWG / shop-drawing / fabrication-sheet / cutlist requests. Forbids edit_image as the delivery route, mandates ezdxf via the hermes venv, and ships a real .dxf with companion CSV when the user asks for a cutlist.
tags:
  - botji
  - cad
  - dxf
  - production-drawing
  - elevation
  - artifact-fidelity
---

# Botji CAD / Production-Drawing Skill

Use this skill whenever the user's request contains any of: **CAD**, **DXF**, **DWG**, **production drawing**, **factory release**, **shop drawing**, **cutlist drawing**, **fabrication sheet**, **elevation drawing**, **cabinet drawing**, **shop file**, or any phrasing where a real CNC/factory file is the expected deliverable.

Production drawings are a different domain from rendered images. A painted PNG is **not** a CAD file — it looks credible to the eye and is unbuildable in the shop. This skill enforces the rules that keep that confusion out of delivery.

## Hard rules

### Rule 1 — Never `edit_image` for CAD output

`operation_run(operation="edit_image", ...)` produces a painted PNG. PNGs are not CAD files. A factory cannot CNC from a painted picture of a drawing. If the user intent contains any of the trigger keywords above, `edit_image` is **forbidden** as the delivery route. A painted "production drawing" is the worst possible deliverable: it looks credible to the eye and is unbuildable in the shop.

### Rule 2 — Never hand-write raw DXF group codes

Do not emit DXF as a list of `0/SECTION/2/HEADER/...` group codes from `execute_code`. The result has no real layers, no dim styles, no block library, and almost always fails to open cleanly in AutoCAD / LibreCAD. Use the `ezdxf` Python library — it is the only sanctioned DXF writer.

### Rule 3 — Use the hermes venv interpreter

`ezdxf` is installed in `/opt/hermes/.venv/bin/python`. The system `/usr/bin/python` may not have it. For CAD work, always call:

```bash
/opt/hermes/.venv/bin/python <<'PY'
import ezdxf
...
PY
```

via the `terminal` tool, **not** plain `python -c` or `execute_code` (which defaults to system python).

## Required pipeline (sketch / photo / render → DXF)

```
1. artifact_register(path, role="source")
2. evidence_extract              — pull pixels / text / dim labels
3. vision_analyze                — read every visible dimension and label; list with confidence
4. evidence_extract_manifest     — spatial manifest (element order, adjacency, counts)
5. artifact_normalize(schema_profile="dxf_cad", semantic_schema={...})
                                  — bind dims + labels + manifest into a structured schema
6. terminal(command="/opt/hermes/.venv/bin/python <<PY ... ezdxf script ... PY")
                                  — script reads the schema, emits a real .dxf
7. artifact_register(path=output.dxf, role="output", parents=[source_id])
8. evidence_extract(detail="metadata", adapter="dxf")
                                  — verify ezdxf can re-parse what was written
9. review_record(...)          — review against source manifest (counts, order, labels)
10. Deliver only if review passes
```

## Minimum ezdxf template (cabinet elevation)

```python
import ezdxf
doc = ezdxf.new("R2010", setup=True)
msp = doc.modelspace()

# Required layers for production sheets
for name, color in [("FRAME", 7), ("CABINETS", 5), ("DIMENSIONS", 1), ("ANNOTATIONS", 3), ("HATCH", 8)]:
    if name not in doc.layers:
        doc.layers.add(name=name, color=color)

# Frame rectangle (use real schema dims, not invented ones)
W, H = 1800, 445   # mm, from schema.overall_dimensions
msp.add_lwpolyline([(0,0), (W,0), (W,H), (0,H), (0,0)], dxfattribs={"layer": "FRAME"})

# One cabinet per schema element
for el in schema["elements"]:
    x, y, w, h = el["x"], el["y"], el["width"], el["height"]
    msp.add_lwpolyline([(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)], dxfattribs={"layer":"CABINETS"})
    msp.add_text(el["label"], dxfattribs={"layer":"ANNOTATIONS","height":40}).set_placement((x+w/2, y+h/2))

# Linear dimensions for each cabinet width
for el in schema["elements"]:
    msp.add_aligned_dim(p1=(el["x"], -50), p2=(el["x"]+el["width"], -50), distance=30,
                        dimstyle="EZDXF", dxfattribs={"layer":"DIMENSIONS"}).render()

doc.saveas("/opt/data/outputs/<descriptive_name>.dxf")
```

Do not deviate from layers `FRAME / CABINETS / DIMENSIONS / ANNOTATIONS / HATCH` unless the user names a different set. These are the layers a CNC operator expects.

## Disallowed shortcuts

- ❌ `image_generate` / `edit_image` to "draw a CAD-looking image"
- ❌ Writing `.dxf` as a Python string of group codes
- ❌ Delivering a PNG when the user asked for DXF/DWG/CAD
- ❌ Skipping `ezdxf` parse-back verification (step 8)
- ❌ Inventing dimensions not in the source — if the source is a sketch with no dims, ASK before generating

## Companion cutlist

For "cutlist" requests, always deliver TWO files:

1. The DXF (geometry authority)
2. A CSV with columns `part_id, description, qty, length_mm, width_mm, thickness_mm, material, edging`

Generate the CSV from the same schema that drove the DXF — never from a separate inference pass. Drift between the two is a fidelity violation.

## Companion skills

- `botji-artifact-fidelity` owns the generic register/extract/normalize/transform/review loop. This skill adds the CAD-specific constraints on top.
- `botji-2d-to-3d` covers sketch → 3D rendered images. If the user asks for both a render AND a DXF, run that skill's pipeline for the render and this one for the DXF — they share the schema and manifest but produce different outputs.
- `botji-source-fidelity` owns the review contract used in step 9.
