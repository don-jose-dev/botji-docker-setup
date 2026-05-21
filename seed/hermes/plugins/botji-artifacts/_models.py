"""Pydantic v2 models for all botji-artifacts tool parameter sets.

Replaces the manual str()/bool()/list() coercion chains in _handlers.py and
the hand-written JSON schema dicts in _schemas.py.

Each model:
- Validates and coerces the raw args dict the agent passes to a tool
- Drives the JSON Schema registered with hermes via model_json_schema()
- Provides IDE type-hints in handlers via params.field_name
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactRegisterParams(BaseModel):
    path: str = Field(description="Absolute path or HERMES_HOME-relative path.")
    declared_type: str = "auto"
    role: Literal["source", "reference", "intermediate", "output", "evidence", "preview"] = "source"
    authority: Literal["user_supplied", "agent_generated", "external", "derived"] = "user_supplied"
    copy_into_registry: bool = True
    parents: list[str] = Field(default_factory=list)
    user_intent: str = ""
    route: str = Field(
        default="",
        description="Declared transform route for provider-generated outputs "
        "(e.g. artifact_transform.edit_image.openai_codex). Required when role=output "
        "and the artifact was produced by a provider tool rather than artifact_transform.",
    )


class ArtifactExtractParams(BaseModel):
    artifact_id: str = Field(description="Legacy botji-artifacts art_* ID. Do not pass Hermes-native src_* IDs.")
    detail: Literal["metadata", "preview", "full"] = "metadata"
    intent: str = "review"
    adapter: str = "auto"


class ArtifactExtractManifestParams(BaseModel):
    artifact_id: str = Field(description="Legacy botji-artifacts art_* ID for an image artifact to analyse.")


class ArtifactNormalizeParams(BaseModel):
    artifact_id: str = Field(description="Legacy botji-artifacts art_* ID. Do not pass Hermes-native src_* IDs.")
    schema_profile: str = Field(
        default="auto",
        description="Domain profile, such as interior_layout, pdf_document, dxf_cad, "
        "text_document, or auto.",
    )
    intent: str = "fidelity"
    semantic_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="Reviewed domain schema extracted from source content "
        "(objects, elements, dimensions, line spans, etc.).",
    )
    hard_requirements: list[str] = Field(
        default_factory=list,
        description="Source facts or explicit user requirements that must pass review.",
    )
    advisory_preferences: list[str] = Field(
        default_factory=list,
        description="Best-effort style or quality preferences; warn but do not block "
        "unless made mandatory.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence IDs used to construct the normalised schema.",
    )


class ArtifactTransformParams(BaseModel):
    source_artifact_ids: list[str] = Field(
        min_length=1,
        description="Legacy botji-artifacts art_* source IDs. Do not pass Hermes-native src_* IDs.",
    )
    contract_id: str = "manual"
    operation: Literal["edit_image", "render_schema", "exact_copy"] = "exact_copy"
    instructions: str = ""
    output_type: Literal["image", "json", "text"] = "image"
    schema_evidence_id: str = Field(
        default="", description="Evidence ID produced by artifact_normalize."
    )
    inline_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        description="Inline schema payload for deterministic render_schema transforms.",
    )
    render_title: str = "Botji artifact schema preview"
    fidelity_mode: Literal["strict", "balanced", "creative"] = "strict"
    provider_route: Literal["auto", "openai_codex"] = Field(
        default="auto",
        description="Use openai_codex for ChatGPT/Codex OAuth, or auto to require "
        "Codex OAuth when available.",
    )
    quality: Literal["low", "medium", "high", "auto"] = "high"
    size: str = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    camera_brief: str = Field(
        default="",
        description="Camera spec for edit_image: body, lens, view angle. "
        "e.g. 'Sony A7 IV · 24mm tilt-shift · front elevation'",
    )
    light_brief: str = Field(
        default="",
        description="Lighting spec for edit_image: quality, direction, colour temp. "
        "e.g. 'soft diffused · front-left 30° · 5500K'",
    )
    mood_brief: str = Field(
        default="",
        description="Photography/rendering genre for edit_image. "
        "e.g. 'architectural interior photography · editorial showroom'",
    )
    subject_inventory: list[str] = Field(
        default_factory=list,
        description="Source element inventory for edit_image, left-to-right/top-to-bottom "
        "with counts and positions.",
    )
    hard_preserve: list[str] = Field(
        default_factory=list,
        description="Layout rules and object constraints that must not change in edit_image output.",
    )
    forbidden_elements: list[str] = Field(
        default_factory=list,
        description="Elements to explicitly forbid in edit_image output — name what "
        "gpt-image-2 is likely to hallucinate for this scene.",
    )
    prior_blocker: str = Field(
        default="",
        description="primary_blocker text from the previous review verdict. When set, "
        "it is prepended as the first FORBIDDEN constraint so the retry directly targets "
        "the prior failure.",
    )
    retry_guidance: str = Field(
        default="",
        description="retry_guidance text from the previous review verdict. When set, "
        "it is promoted to a critical hard-preserve correction in the next edit_image brief.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("source_artifact_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, v: Any) -> list[str]:
        """Accept a bare string ID as well as a list."""
        if isinstance(v, str):
            return [v]
        return v

    @field_validator(
        "camera_brief", "light_brief", "mood_brief",
        "instructions", "prior_blocker", "retry_guidance",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("subject_inventory", "hard_preserve", "forbidden_elements", mode="before")
    @classmethod
    def _clean_str_list(cls, v: Any) -> list[str]:
        return [s.strip() for s in (v or []) if str(s).strip()]


class ArtifactReviewParams(BaseModel):
    source_artifact_ids: list[str] = Field(
        description="Legacy botji-artifacts art_* source IDs. Do not pass Hermes-native src_* IDs."
    )
    output_artifact_id: str = Field(
        description="Legacy botji-artifacts art_* output ID. Do not pass Hermes-native out_* IDs."
    )
    contract_id: str = "manual"
    evidence_ids: list[str] = Field(default_factory=list)
    fidelity_requirements: list[str] = Field(
        default_factory=list,
        description="Hard source/content/layout requirements that the output must "
        "preserve from the source.",
    )
    use_openai_vision: bool = True
    require_vision_api: bool = False
    review_provider_route: Literal["auto", "openai_codex"] = "auto"

    @field_validator("source_artifact_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return v


class ArtifactListParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    role: str | None = None
    adapter: str | None = None


class ArtifactReadParams(BaseModel):
    artifact_id: str = Field(description="Legacy botji-artifacts art_* ID.")
