---
name: botji-image-brief
version: 1.0.0
description: Image generation brief — camera, lighting, mood, subject, forbidden elements. Reference when constructing any gpt-image-2 prompt.
requires_tools: []
---

# Image Generation Brief

## Required structure

Every `artifact_transform(operation="edit_image")` or `image_generate` call must use this structure. Never freeform prose.

```
CAMERA: [body e.g. Canon R5 | Sony A7 IV] · [lens e.g. 24mm tilt-shift | 35mm f/2.8] · [view e.g. front elevation | 3/4 perspective]
LIGHT:  [quality e.g. soft diffused | hard direct] · [direction e.g. front-left 30°] · [color temp e.g. 5500K overcast | 3200K warm]
MOOD:   [genre e.g. architectural interior photography | commercial product | editorial showroom | technical illustration]

SUBJECT (inventory left-to-right, top-to-bottom from source):
  - [element 1: count, position, material if known]
  - [element 2]

HARD PRESERVE:
  - [structural elements and layout]
  - Object count: exactly [N] [elements]
  - [spatial relationships and named constraints]

ALLOWED CHANGES:
  - [explicit list of what may change]

FORBIDDEN:
  - Do not add any objects not visible in the source image
  - Do not add [specific: plants / furniture / people / extra panels / decorative objects]
  - Do not remove or reorder [specific elements]
  - Do not extend any element beyond its source boundary
```

## Genre shortcuts

**Interiors / cabinetry:** `architectural interior photography, front elevation, shot on 24mm tilt-shift, soft overcast window light, white balance 5500K, editorial clean`

**Product:** `commercial product photography, studio three-point lighting, white cyclorama, shot on 90mm macro, no shadows outside product`

**Landscape / garden:** `landscape architecture photography, eye-level perspective, natural overcast diffused, mid-morning 6000K`

**Technical drawing → 3D:** `technical product render, engineering visualization, studio neutral, no harsh shadows`

## Checklist before sending

- Camera body + lens specified (not "3D render" or "photorealistic")
- Lighting quality, direction, and color temp named
- Mood/genre is a photography or rendering style, not an adjective
- SUBJECT uses counts and positions from source (not prose)
- HARD PRESERVE includes object count and layout rules
- FORBIDDEN names what gpt-image-2 is likely to hallucinate for this scene type
- Source image passed as pixel input (not just described in text)

## Claim level

Photo→3D transforms are always `reviewed`, never `verified`.

```json
"fidelity_scores": {
  "transform_contract_fidelity_percent": 100,
  "byte_exact_file_fidelity_percent": 0,
  "claim_type": "transform_contract_fidelity",
  "schema_extraction_confidence": "inferred"
}
```
