"""Botji generic artifact fidelity plugin.

The plugin turns files into durable Botji artifacts before the agent reasons
about them. It records checksums, adapter evidence, lineage, Codex-backed image
transforms, and review receipts.

Package layout
──────────────
_constants.py   — shared constants (models, suffix sets, axis names)
_schemas.py     — tool JSON schema definitions
_utils.py       — path helpers, ID generation, file utilities
_detect.py      — MIME type and adapter detection
_registry.py    — artifact registry I/O (index, evidence, registration)
_extraction.py  — schema normalisation and per-adapter metadata extraction
_normalization.py — exact-copy and schema-render transform operations
_rendering.py   — output artifact creation, PNG preview, schema markdown
_codex.py       — Codex OAuth client, image generation, vision comparison
_vision.py      — vision review prompts, JSON parsing, conflict classification
_review.py      — fidelity review logic, comparators, and axes assembly
_handlers.py    — tool handler functions (thin dispatchers)
prompts/        — AI instruction templates as .md files
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add this directory to sys.path so sibling submodules are importable.
_PLUGIN_DIR = str(Path(__file__).parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from _schemas import (  # noqa: E402
    ARTIFACT_REGISTER_SCHEMA,
    ARTIFACT_EXTRACT_SCHEMA,
    ARTIFACT_NORMALIZE_SCHEMA,
    ARTIFACT_TRANSFORM_SCHEMA,
    ARTIFACT_REVIEW_SCHEMA,
    ARTIFACT_LIST_SCHEMA,
    ARTIFACT_READ_SCHEMA,
)
from _handlers import (  # noqa: E402
    _handle_artifact_register,
    _handle_artifact_extract,
    _handle_artifact_normalize,
    _handle_artifact_transform,
    _handle_artifact_review,
    _handle_artifact_list,
    _handle_artifact_read,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="artifact_register",
        toolset="file",
        schema=ARTIFACT_REGISTER_SCHEMA,
        handler=_handle_artifact_register,
        description=ARTIFACT_REGISTER_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_extract",
        toolset="file",
        schema=ARTIFACT_EXTRACT_SCHEMA,
        handler=_handle_artifact_extract,
        description=ARTIFACT_EXTRACT_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_normalize",
        toolset="file",
        schema=ARTIFACT_NORMALIZE_SCHEMA,
        handler=_handle_artifact_normalize,
        description=ARTIFACT_NORMALIZE_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_transform",
        toolset="file",
        schema=ARTIFACT_TRANSFORM_SCHEMA,
        handler=_handle_artifact_transform,
        description=ARTIFACT_TRANSFORM_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_review",
        toolset="file",
        schema=ARTIFACT_REVIEW_SCHEMA,
        handler=_handle_artifact_review,
        description=ARTIFACT_REVIEW_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_list",
        toolset="file",
        schema=ARTIFACT_LIST_SCHEMA,
        handler=_handle_artifact_list,
        description=ARTIFACT_LIST_SCHEMA["description"],
    )
    ctx.register_tool(
        name="artifact_read",
        toolset="file",
        schema=ARTIFACT_READ_SCHEMA,
        handler=_handle_artifact_read,
        description=ARTIFACT_READ_SCHEMA["description"],
    )
