"""Tool schema definitions for all botji-artifacts tools."""
from __future__ import annotations
from typing import Any


def _tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters}


ARTIFACT_REGISTER_SCHEMA = _tool_schema(
    "artifact_register",
    "Register a local source/output file as a Botji artifact with checksum, type, adapter, and lineage.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path or HERMES_HOME-relative path."},
            "declared_type": {"type": "string", "default": "auto"},
            "role": {
                "type": "string",
                "enum": ["source", "reference", "intermediate", "output", "evidence", "preview"],
                "default": "source",
            },
            "authority": {
                "type": "string",
                "enum": ["user_supplied", "agent_generated", "external", "derived"],
                "default": "user_supplied",
            },
            "copy_into_registry": {"type": "boolean", "default": True},
            "parents": {"type": "array", "items": {"type": "string"}},
            "user_intent": {"type": "string"},
            "route": {
                "type": "string",
                "description": "Declared transform route for provider-generated outputs (e.g. artifact_transform.edit_image.openai_codex). Required when role=output and the artifact was produced by a provider tool rather than artifact_transform.",
            },
        },
        "required": ["path"],
    },
)

ARTIFACT_EXTRACT_SCHEMA = _tool_schema(
    "artifact_extract",
    "Extract deterministic metadata/evidence from a registered artifact using the selected adapter.",
    {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "detail": {"type": "string", "enum": ["metadata", "preview", "full"], "default": "metadata"},
            "intent": {"type": "string", "default": "review"},
            "adapter": {"type": "string", "default": "auto"},
        },
        "required": ["artifact_id"],
    },
)

ARTIFACT_NORMALIZE_SCHEMA = _tool_schema(
    "artifact_normalize",
    "Create a typed schema-first fidelity contract for any artifact adapter: image, PDF, text, DXF/CAD, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, or binary.",
    {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "schema_profile": {
                "type": "string",
                "default": "auto",
                "description": "Optional domain profile, such as interior_layout, pdf_document, dxf_cad, text_document, or auto.",
            },
            "intent": {"type": "string", "default": "fidelity"},
            "semantic_schema": {
                "type": "object",
                "description": "Optional reviewed domain schema extracted from source content, such as modules, objects, dimensions, or line spans.",
            },
            "hard_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source facts or explicit user requirements that must pass review.",
            },
            "advisory_preferences": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Best-effort style or quality preferences that should warn, not block, unless made mandatory.",
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence IDs used to construct the normalized schema.",
            },
        },
        "required": ["artifact_id"],
    },
)

ARTIFACT_EXTRACT_MANIFEST_SCHEMA = _tool_schema(
    "artifact_extract_manifest",
    "Extract a structured spatial manifest from an image artifact using vision. Returns scene_type, source_modality, element list (in left-to-right order per wall/zone), adjacency constraints, layout hints, and pre-built fidelity_requirements ready for artifact_review. Use before artifact_transform for any sketch-to-render or fidelity transform.",
    {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "ID of an image artifact to analyse."},
        },
        "required": ["artifact_id"],
    },
)

ARTIFACT_TRANSFORM_SCHEMA = _tool_schema(
    "artifact_transform",
    "Create a source-aware derivative artifact. Default behavior is byte-exact preservation; schema renders are deterministic; image edits require an explicit edit_image operation and use a real source-image provider route.",
    {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "default": "manual"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "operation": {"type": "string", "enum": ["edit_image", "render_schema", "exact_copy"], "default": "exact_copy"},
            "instructions": {"type": "string"},
            "output_type": {"type": "string", "enum": ["image", "json", "text"], "default": "image"},
            "schema_evidence_id": {"type": "string", "description": "Evidence ID produced by artifact_normalize."},
            "schema": {"type": "object", "description": "Inline schema payload for deterministic render_schema transforms."},
            "render_title": {"type": "string", "default": "Botji artifact schema preview"},
            "fidelity_mode": {"type": "string", "enum": ["strict", "balanced", "creative"], "default": "strict"},
            "provider_route": {
                "type": "string",
                "enum": ["auto", "openai_codex"],
                "default": "auto",
                "description": "Use openai_codex for ChatGPT/Codex OAuth, or auto to require Codex OAuth when available.",
            },
            "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"], "default": "high"},
            "size": {"type": "string", "default": "auto"},
            "output_format": {"type": "string", "enum": ["png", "jpeg", "webp"], "default": "png"},
            "camera_brief": {
                "type": "string",
                "description": "Camera spec for edit_image: body, lens, view angle. e.g. 'Sony A7 IV · 24mm tilt-shift · front elevation'",
            },
            "light_brief": {
                "type": "string",
                "description": "Lighting spec for edit_image: quality, direction, color temp. e.g. 'soft diffused · front-left 30° · 5500K'",
            },
            "mood_brief": {
                "type": "string",
                "description": "Photography/rendering genre for edit_image. e.g. 'architectural interior photography · editorial showroom'",
            },
            "subject_inventory": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source element inventory for edit_image, left-to-right/top-to-bottom with counts and positions.",
            },
            "hard_preserve": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Layout rules and object constraints that must not change in edit_image output.",
            },
            "forbidden_elements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Elements to explicitly forbid in edit_image output — name what gpt-image-2 is likely to hallucinate for this scene.",
            },
            "prior_blocker": {
                "type": "string",
                "description": "primary_blocker text from the previous review verdict. When set, it is prepended as the first FORBIDDEN constraint so the retry directly targets the prior failure.",
            },
        },
        "required": ["source_artifact_ids"],
    },
)

ARTIFACT_REVIEW_SCHEMA = _tool_schema(
    "artifact_review",
    "Persist a source-fidelity review receipt for generated artifacts. Can use Codex vision review for image pairs.",
    {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string", "default": "manual"},
            "source_artifact_ids": {"type": "array", "items": {"type": "string"}},
            "output_artifact_id": {"type": "string"},
            "required_axes": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "fidelity_requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hard source/content/layout requirements that the output must preserve from the source.",
            },
            "use_openai_vision": {"type": "boolean", "default": True},
            "require_vision_api": {"type": "boolean", "default": False},
            "review_provider_route": {
                "type": "string",
                "enum": ["auto", "openai_codex"],
                "default": "auto",
            },
        },
        "required": ["source_artifact_ids", "output_artifact_id"],
    },
)

ARTIFACT_LIST_SCHEMA = _tool_schema(
    "artifact_list",
    "List recent Botji artifacts from the registry.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "role": {"type": "string"},
            "adapter": {"type": "string"},
        },
    },
)

ARTIFACT_READ_SCHEMA = _tool_schema(
    "artifact_read",
    "Read one artifact registry record by artifact ID.",
    {
        "type": "object",
        "properties": {"artifact_id": {"type": "string"}},
        "required": ["artifact_id"],
    },
)

