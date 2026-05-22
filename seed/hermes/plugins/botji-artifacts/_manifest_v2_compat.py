"""v1 prose manifest ↔ v2 typed manifest back-compat converters.
Used by the C2/C3 adapter layer. Round-trip preserves structural fields. When
prose can't classify to a controlled-vocab type, status=inferred + type='other'
+ evidence=<prose>.
"""
from __future__ import annotations

import re
from typing import Any, get_args

from _manifest_v2 import (
    AdjacencyConstraint, Element, ElementType, Manifest, OpeningConstraint,
    Position, Zone, _serialise,
)

_TYPES = set(get_args(ElementType))
_ZONES = set(get_args(Zone))
_ZONE_ALIASES = {"centre": "center", "freestanding": "floor"}
_POS_VALID = {"left", "center", "right", "near_corner", "unknown"}
_POS_ALIAS = {"left_side": "left", "right_side": "right"}
_SLUG = re.compile(r"[^a-z0-9]+")
_WORDS = re.compile(r"[a-z][a-z]+")


def _slug_id(label: str, fallback: str) -> str:
    s = _SLUG.sub("_", label.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"el_{fallback}"
    return (s[:64].rstrip("_") or fallback)


def _classify(label: str) -> tuple[str, str]:
    """(type, status): visible when a controlled type matched, inferred else."""
    joined = "_".join(_WORDS.findall(label.lower()))
    flat = joined.replace("_", "")
    for t in _TYPES:
        if t != "other" and (t in joined or t.replace("_", "") in flat):
            return t, "visible"
    return "other", "inferred"


def _zone_of(raw: str) -> str:
    z = _ZONE_ALIASES.get((raw or "").strip().lower().replace(" ", "_"),
                          (raw or "").strip().lower().replace(" ", "_"))
    return z if z in _ZONES else "center"


def _pos_alias(raw: str) -> str:
    p = _POS_ALIAS.get(
        raw.strip().lower().replace(" ", "_").replace("-", "_"),
        raw.strip().lower().replace(" ", "_").replace("-", "_"),
    )
    return p if p in _POS_VALID else "unknown"


def _v1_element(el: dict, idx: int) -> Element:
    label = str(el.get("label") or el.get("id") or f"el_{idx}").strip()
    etype, status = _classify(label)
    return Element(
        element_id=_slug_id(str(el.get("id") or label), f"{idx:02d}"),
        type=etype, zone=_zone_of(str(el.get("wall_or_zone") or "")),  # type: ignore[arg-type]
        required=True, status=status,  # type: ignore[arg-type]
        evidence=(label if status == "inferred" else str(el.get("notes") or "")).strip()[:300],
        position=Position(order_in_zone=int(el.get("position_index") or idx)),
    )


def prose_to_typed(manifest: dict[str, Any]) -> Manifest:
    """Best-effort v1 → v2. Uncertain rows carry status=inferred + evidence=<prose>."""
    return Manifest(
        scene_type=str(manifest.get("scene_type") or "unknown"),
        source_modality=str(manifest.get("source_modality") or "sketch"),  # type: ignore[arg-type]
        elements=[_v1_element(el, idx) for idx, el in enumerate(
            manifest.get("elements") or [], start=1) if isinstance(el, dict)],
        adjacency_constraints=[AdjacencyConstraint(
            elem_a=str(a.get("elem_a") or ""), elem_b=str(a.get("elem_b") or ""),
            relation=str(a.get("relation") or "directly_adjacent"),  # type: ignore[arg-type]
            zone=_zone_of(str(a.get("wall_or_zone") or "")),  # type: ignore[arg-type]
        ) for a in (manifest.get("adjacency_constraints") or []) if isinstance(a, dict)],
        opening_constraints=[OpeningConstraint(
            element_id=str(o.get("element_id") or ""), room_or_zone=str(o.get("room_or_zone") or ""),
            opening_type=str(o.get("opening_type") or "unknown"),  # type: ignore[arg-type]
            zone=_zone_of(str(o.get("wall_or_side") or "")),  # type: ignore[arg-type]
            position_on_wall=_pos_alias(str(o.get("position_on_wall") or "unknown")),  # type: ignore[arg-type]
            swing_or_handing=str(o.get("swing_or_handing") or "unknown"),
            notes=str(o.get("notes") or ""),
        ) for o in (manifest.get("opening_constraints") or []) if isinstance(o, dict)],
        layout_hints=list(manifest.get("layout_hints") or []),
        fidelity_requirements=list(manifest.get("fidelity_requirements") or []),
    )


def typed_to_prose(m: Manifest) -> dict[str, Any]:
    """v2 → v1 dict the existing review code expects. status=absent rows are dropped."""
    data = _serialise(m)
    elements = [{
        "id": e["element_id"], "label": e.get("evidence") or e["type"].replace("_", " "),
        "wall_or_zone": e["zone"],
        "position_index": (e.get("position") or {}).get("order_in_zone") or 1,
        "notes": "" if e["status"] == "visible" else f"status={e['status']}",
    } for e in data["elements"] if e["status"] != "absent"]
    return {
        "scene_type": data["scene_type"], "source_modality": data["source_modality"],
        "elements": elements, "element_count": len(elements),
        "adjacency_constraints": [{
            "elem_a": a["elem_a"], "elem_b": a["elem_b"],
            "relation": a["relation"], "wall_or_zone": a["zone"],
        } for a in data.get("adjacency_constraints") or []],
        "opening_constraints": [{
            "element_id": o["element_id"], "room_or_zone": o["room_or_zone"],
            "opening_type": o["opening_type"], "wall_or_side": o["zone"],
            "position_on_wall": o["position_on_wall"],
            "swing_or_handing": o["swing_or_handing"], "notes": o.get("notes", ""),
        } for o in data.get("opening_constraints") or []],
        "layout_hints": list(data.get("layout_hints") or []),
        "fidelity_requirements": list(data.get("fidelity_requirements") or []),
    }
