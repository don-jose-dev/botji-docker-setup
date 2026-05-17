"""Tool schema definitions for all botji-artifacts tools.

Schemas are auto-generated from Pydantic models in _models.py.
_tool_schema() strips Pydantic's internal 'title' metadata so the
hermes plugin registry receives clean OpenAI-style tool schemas.
"""
from __future__ import annotations

from typing import Any

from _models import (
    ArtifactExtractManifestParams,
    ArtifactExtractParams,
    ArtifactListParams,
    ArtifactNormalizeParams,
    ArtifactReadParams,
    ArtifactRegisterParams,
    ArtifactReviewParams,
    ArtifactTransformParams,
)


def _tool_schema(name: str, description: str, parameters: Any) -> dict[str, Any]:
    """Build a hermes tool schema dict.

    ``parameters`` may be a Pydantic model class (preferred) or a raw dict
    (kept for back-compat during migration). When a model class is passed,
    its JSON Schema is generated automatically and Pydantic's internal
    ``title`` fields are stripped so the output matches the hand-written
    format hermes expects.
    """
    if isinstance(parameters, type):
        schema = parameters.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        parameters = schema
    return {"name": name, "description": description, "parameters": parameters}


ARTIFACT_REGISTER_SCHEMA = _tool_schema(
    "artifact_register",
    "Register a local source/output file as a Botji artifact with checksum, type, adapter, and lineage.",
    ArtifactRegisterParams,
)

ARTIFACT_EXTRACT_SCHEMA = _tool_schema(
    "artifact_extract",
    "Extract deterministic metadata/evidence from a registered artifact using the selected adapter.",
    ArtifactExtractParams,
)

ARTIFACT_EXTRACT_MANIFEST_SCHEMA = _tool_schema(
    "artifact_extract_manifest",
    "Extract a structured spatial manifest from an image artifact using vision. Returns scene_type, "
    "source_modality, element list (in left-to-right order per wall/zone), adjacency constraints, "
    "layout hints, and pre-built fidelity_requirements ready for artifact_review. Use before "
    "artifact_transform for any sketch-to-render or fidelity transform.",
    ArtifactExtractManifestParams,
)

ARTIFACT_NORMALIZE_SCHEMA = _tool_schema(
    "artifact_normalize",
    "Create a typed schema-first fidelity contract for any artifact adapter: image, PDF, text, "
    "DXF/CAD, DOCX, XLSX, HTML, SVG, STEP, IFC, ZIP, audio, video, or binary.",
    ArtifactNormalizeParams,
)

ARTIFACT_TRANSFORM_SCHEMA = _tool_schema(
    "artifact_transform",
    "Create a source-aware derivative artifact. Default behavior is byte-exact preservation; "
    "schema renders are deterministic; image edits require an explicit edit_image operation "
    "and use a real source-image provider route.",
    ArtifactTransformParams,
)

ARTIFACT_REVIEW_SCHEMA = _tool_schema(
    "artifact_review",
    "Persist a source-fidelity review receipt for generated artifacts. Can use Codex vision "
    "review for image pairs.",
    ArtifactReviewParams,
)

ARTIFACT_LIST_SCHEMA = _tool_schema(
    "artifact_list",
    "List recent Botji artifacts from the registry.",
    ArtifactListParams,
)

ARTIFACT_READ_SCHEMA = _tool_schema(
    "artifact_read",
    "Read one artifact registry record by artifact ID.",
    ArtifactReadParams,
)
