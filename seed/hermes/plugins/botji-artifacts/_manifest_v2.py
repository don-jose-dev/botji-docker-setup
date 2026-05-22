"""Typed manifest v2 — Pydantic models. Phase C1 scaffold only.
v1 stored ``label = "base cabinets and countertop"`` and review fuzzy-matched
that prose against vision output. v2 stores ``type = "countertop", zone =
"back_wall"`` so review compares typed rows by ``(element_id, type, zone)``.
Schema: ``seed/hermes/schemas/manifest_v2.schema.json``. Converters:
``_manifest_v2_compat.py``. Docs: ``docs/MANIFEST_V2.md``."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid", frozen=False, validate_default=True)
_ELEMENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*[0-9a-z]$")

# Controlled vocabs — kept aligned with manifest_v2.schema.json $defs.
# Update both the schema enum AND this literal when adding a type.
ElementType = Literal[
    "base_cabinet", "wall_cabinet", "tall_cabinet", "larder", "pantry",
    "countertop", "backsplash", "sink", "tap", "hob", "cooktop", "oven",
    "oven_stack", "microwave", "extractor", "hood", "fridge", "freezer",
    "dishwasher", "appliance_tower", "island", "peninsula", "breakfast_bar",
    "bar_stool", "shelf", "open_shelf", "drawer_unit", "filler_panel",
    "plinth", "kickboard", "cornice", "valance",
    "wardrobe_unit", "hanging_rail", "shoe_rack", "drawer_module",
    "door_module", "mirror_panel", "internal_drawer", "shelf_module",
    "sofa", "armchair", "coffee_table", "side_table", "tv_unit",
    "media_console", "bookshelf", "rug", "floor_lamp", "pendant_light",
    "ceiling_light", "wall_sconce", "artwork", "plant",
    "door", "doorway", "entry_opening", "window", "arch", "threshold",
    "wall", "floor", "ceiling",
    "other",
]
Zone = Literal[
    "left_wall", "right_wall", "back_wall", "front_wall",
    "floor", "ceiling", "center", "island",
]
Status = Literal["visible", "inferred", "absent"]


class Position(BaseModel):
    model_config = _STRICT
    order_in_zone: int = Field(ge=1)


class Element(BaseModel):
    model_config = _STRICT
    element_id: str = Field(min_length=2, max_length=64)
    type: ElementType
    zone: Zone
    required: bool = True
    status: Status = "visible"
    evidence: str = Field(default="", max_length=300)
    position: Position | None = None

    @field_validator("element_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _ELEMENT_ID_RE.match(v):
            raise ValueError(f"element_id must be kebab-style snake_case: {v!r}")
        return v


class AdjacencyConstraint(BaseModel):
    model_config = _STRICT
    elem_a: str
    elem_b: str
    relation: Literal["directly_adjacent", "separated_by_gap", "overlapping"]
    zone: Zone


class OpeningConstraint(BaseModel):
    model_config = _STRICT
    element_id: str
    room_or_zone: str
    opening_type: Literal["door", "entry", "window", "arch", "unknown"]
    zone: Zone
    position_on_wall: Literal["left", "center", "right", "near_corner", "unknown"]
    swing_or_handing: str
    notes: str = ""


class Manifest(BaseModel):
    model_config = _STRICT
    version: Literal["v2"] = "v2"
    scene_type: str
    source_modality: Literal["sketch", "floor_plan", "photo", "render", "schematic"]
    elements: list[Element] = Field(default_factory=list)
    adjacency_constraints: list[AdjacencyConstraint] = Field(default_factory=list)
    opening_constraints: list[OpeningConstraint] = Field(default_factory=list)
    layout_hints: list[str] = Field(default_factory=list)
    fidelity_requirements: list[str] = Field(default_factory=list)


def _serialise(m: Manifest) -> dict[str, Any]:
    return m.model_dump(exclude_none=True, mode="json")
