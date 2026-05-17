"""Schema normalisation and artifact extraction (per-adapter metadata lives in _metadata.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from _constants import (
    ARTIFACT_SCHEMA_VERSION, MAX_SOURCE_ARTIFACTS,
)
from _utils import _json, _now, _new_id, _artifact_root, _hermes_home, _sha256
from _registry import _store_evidence
from _metadata import (
    _image_metadata, _text_metadata, _pdf_metadata, _dxf_metadata,
    _docx_metadata, _xlsx_metadata, _html_metadata, _svg_metadata,
    _step_metadata, _ifc_metadata, _zip_metadata, _audio_metadata,
    _video_metadata, _binary_metadata,
)


def _build_normalized_schema(
    artifact: dict[str, Any],
    *,
    schema_profile: str,
    intent: str,
    semantic_schema: dict[str, Any],
    hard_requirements: list[str],
    advisory_preferences: list[str],
    evidence_ids: list[str],
) -> dict[str, Any]:
    adapter = str(artifact.get("adapter") or "binary")
    profile = schema_profile if schema_profile != "auto" else _default_schema_profile(adapter)
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "profile": profile,
        "intent": intent,
        "artifact": {
            "artifact_id": artifact["artifact_id"],
            "role": artifact.get("role"),
            "adapter": adapter,
            "detected_type": artifact.get("detected_type"),
            "path": artifact.get("path"),
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "authority": artifact.get("authority"),
            "created_at": artifact.get("created_at"),
        },
        "deterministic": _deterministic_schema_for_artifact(artifact),
        "semantic_schema": semantic_schema,
        "fidelity_contract": {
            "hard_requirements": hard_requirements,
            "advisory_preferences": advisory_preferences,
            "claim_boundary": _claim_boundary_for_adapter(adapter),
        },
        "transform_policy": _transform_policy_for_adapter(adapter),
        "evidence_basis": list(dict.fromkeys([*(artifact.get("evidence_ids") or []), *evidence_ids])),
    }


def _default_schema_profile(adapter: str) -> str:
    return {
        "image": "raster_image",
        "pdf": "pdf_document",
        "text": "text_document",
        "dxf": "cad_dxf",
        "docx": "word_document",
        "xlsx": "spreadsheet_workbook",
        "html": "html_document",
        "svg": "svg_vector_graphic",
        "step": "step_cad_model",
        "ifc": "ifc_bim_model",
        "zip": "zip_archive",
        "audio": "audio_media",
        "video": "video_media",
        "binary": "binary_file",
    }.get(adapter, "binary_file")


def _claim_boundary_for_adapter(adapter: str) -> str:
    if adapter == "image":
        return "Raster pixels can support visual/layout review; exact dimensions require supplied measurements or vector/CAD evidence."
    if adapter == "pdf":
        return "Text and page geometry are deterministic when extractable; scanned pages require OCR or vision review for content claims."
    if adapter == "dxf":
        return "DXF entities, layers, and units are deterministic geometry authority when parsed successfully."
    if adapter == "text":
        return "Line and character spans are deterministic; semantic summaries are reviewed unless separately validated."
    if adapter == "docx":
        return "DOCX package structure, paragraphs, tables, and extracted text are deterministic from OpenXML; visual pagination is application-dependent."
    if adapter == "xlsx":
        return "XLSX workbook sheets, dimensions, cells, formulas, and shared strings are deterministic from OpenXML; recalculated values require spreadsheet-engine validation."
    if adapter == "html":
        return "HTML tags, title, headings, links, images, and extracted text are deterministic; browser layout requires rendering validation."
    if adapter == "svg":
        return "SVG viewport, viewBox, elements, text, and vector primitives are deterministic from XML; raster appearance depends on renderer support."
    if adapter == "step":
        return "STEP entities, schema, and header metadata are deterministic from ISO-10303 text; engineering semantics require domain-specific validation."
    if adapter == "ifc":
        return "IFC entities, schema, spatial hierarchy counts, and property text are deterministic from STEP text; code compliance requires domain review."
    if adapter == "zip":
        return "ZIP entry names, CRCs, sizes, timestamps, and archive structure are deterministic; nested content claims require registering contained files."
    if adapter == "audio":
        return "Audio container/header metadata is deterministic; speech/music content requires transcription or signal analysis."
    if adapter == "video":
        return "Video container/header metadata is deterministic; scene/action content requires frame extraction or vision review."
    if adapter == "binary":
        return "Checksum, size, MIME guess, and byte signatures are deterministic; file-content claims require a structured adapter."
    return "Only file metadata and checksum are deterministic for this adapter."


def _transform_policy_for_adapter(adapter: str) -> dict[str, Any]:
    if adapter == "image":
        return {
            "preferred_routes": ["exact_copy", "render_schema", "artifact_transform.edit_image.openai_codex"],
            "forbidden_routes": ["prompt_only_image_generate_for_source_bound_work"],
        }
    if adapter == "dxf":
        return {
            "preferred_routes": ["exact_copy", "render_schema", "dxf_vector_transform"],
            "forbidden_routes": ["raster_only_dimension_claims"],
        }
    if adapter == "pdf":
        return {
            "preferred_routes": ["exact_copy", "extract_text_tables", "render_schema", "ocr_or_vision_for_scanned_pages"],
            "forbidden_routes": ["claiming_unextracted_text_or_tables"],
        }
    if adapter == "text":
        return {
            "preferred_routes": ["exact_copy", "line_span_transform", "render_schema"],
            "forbidden_routes": ["uncited_rewrite_when_fidelity_required"],
        }
    if adapter == "docx":
        return {
            "preferred_routes": ["exact_copy", "openxml_transform", "render_schema"],
            "forbidden_routes": ["flattened_text_rewrite_without_openxml_review"],
        }
    if adapter == "xlsx":
        return {
            "preferred_routes": ["exact_copy", "openxml_workbook_transform", "render_schema"],
            "forbidden_routes": ["manual_cell_claims_without_sheet_range_evidence"],
        }
    if adapter == "html":
        return {
            "preferred_routes": ["exact_copy", "dom_transform", "render_schema", "browser_render_review"],
            "forbidden_routes": ["claiming_layout_without_browser_render"],
        }
    if adapter == "svg":
        return {
            "preferred_routes": ["exact_copy", "xml_vector_transform", "render_schema"],
            "forbidden_routes": ["raster_only_vector_claims"],
        }
    if adapter == "step":
        return {
            "preferred_routes": ["exact_copy", "step_entity_transform", "render_schema"],
            "forbidden_routes": ["invented_cad_geometry_without_step_entities"],
        }
    if adapter == "ifc":
        return {
            "preferred_routes": ["exact_copy", "ifc_entity_transform", "render_schema"],
            "forbidden_routes": ["building_claims_without_ifc_entity_evidence"],
        }
    if adapter == "zip":
        return {
            "preferred_routes": ["exact_copy", "archive_manifest_transform", "register_nested_files", "render_schema"],
            "forbidden_routes": ["nested_content_claims_without_extraction"],
        }
    if adapter == "audio":
        return {
            "preferred_routes": ["exact_copy", "audio_metadata_transform", "transcription_then_review", "render_schema"],
            "forbidden_routes": ["speech_or_content_claims_without_transcription"],
        }
    if adapter == "video":
        return {
            "preferred_routes": ["exact_copy", "video_metadata_transform", "frame_extraction_then_review", "render_schema"],
            "forbidden_routes": ["scene_or_action_claims_without_frame_review"],
        }
    if adapter == "binary":
        return {
            "preferred_routes": ["exact_copy", "checksum_metadata_transform", "render_schema"],
            "forbidden_routes": ["content_claims_without_adapter"],
        }
    return {"preferred_routes": ["metadata_preserving_transform"], "forbidden_routes": ["unsupported_content_claims"]}


def _deterministic_schema_for_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["path"])
    adapter = artifact.get("adapter")
    base = {
        "path": str(path),
        "sha256": artifact.get("sha256"),
        "detected_type": artifact.get("detected_type"),
        "size_bytes": path.stat().st_size if path.exists() else artifact.get("size_bytes"),
    }
    if adapter == "image":
        return {**base, **_image_metadata(path)}
    if adapter == "text":
        return {**base, **_text_metadata(path)}
    if adapter == "pdf":
        return {**base, **_pdf_metadata(path)}
    if adapter == "dxf":
        return {**base, **_dxf_metadata(path)}
    if adapter == "docx":
        return {**base, **_docx_metadata(path)}
    if adapter == "xlsx":
        return {**base, **_xlsx_metadata(path)}
    if adapter == "html":
        return {**base, **_html_metadata(path)}
    if adapter == "svg":
        return {**base, **_svg_metadata(path)}
    if adapter == "step":
        return {**base, **_step_metadata(path)}
    if adapter == "ifc":
        return {**base, **_ifc_metadata(path)}
    if adapter == "zip":
        return {**base, **_zip_metadata(path)}
    if adapter == "audio":
        return {**base, **_audio_metadata(path)}
    if adapter == "video":
        return {**base, **_video_metadata(path)}
    if adapter == "binary":
        return {**base, **_binary_metadata(path)}
    return {**base, "adapter": adapter or "binary"}


def _extract_artifact(artifact: dict[str, Any], detail: str, intent: str) -> dict[str, Any]:
    path = Path(artifact["path"])
    adapter = artifact.get("adapter")
    if adapter == "image":
        return _extract_image(artifact, path, detail, intent)
    if adapter == "text":
        return _extract_text(artifact, path, detail, intent)
    if adapter == "pdf":
        return _extract_pdf(artifact, path, detail, intent)
    if adapter == "dxf":
        return _extract_dxf(artifact, path, detail, intent)
    if adapter == "docx":
        return _extract_structured(artifact, path, detail, intent, extractor="openxml_docx", metadata=_docx_metadata(path))
    if adapter == "xlsx":
        return _extract_structured(artifact, path, detail, intent, extractor="openxml_xlsx", metadata=_xlsx_metadata(path))
    if adapter == "html":
        return _extract_structured(artifact, path, detail, intent, extractor="html_parser", metadata=_html_metadata(path))
    if adapter == "svg":
        return _extract_structured(artifact, path, detail, intent, extractor="svg_xml_parser", metadata=_svg_metadata(path))
    if adapter == "step":
        return _extract_structured(artifact, path, detail, intent, extractor="step_text_parser", metadata=_step_metadata(path))
    if adapter == "ifc":
        return _extract_structured(artifact, path, detail, intent, extractor="ifc_step_parser", metadata=_ifc_metadata(path))
    if adapter == "zip":
        return _extract_structured(artifact, path, detail, intent, extractor="zip_manifest", metadata=_zip_metadata(path))
    if adapter == "audio":
        return _extract_structured(artifact, path, detail, intent, extractor="audio_header_parser", metadata=_audio_metadata(path))
    if adapter == "video":
        return _extract_structured(artifact, path, detail, intent, extractor="video_container_parser", metadata=_video_metadata(path))
    if adapter == "binary":
        return _extract_structured(artifact, path, detail, intent, extractor="binary_signature", metadata=_binary_metadata(path))
    return _store_evidence(
        artifact,
        extractor="file_metadata",
        claim_level="verified",
        summary="Basic file metadata extracted.",
        data={
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": artifact.get("sha256"),
            "detected_type": artifact.get("detected_type"),
            "detail": detail,
            "intent": intent,
        },
    )


def _extract_structured(
    artifact: dict[str, Any],
    path: Path,
    detail: str,
    intent: str,
    *,
    extractor: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    data = {**metadata, "detail": detail, "intent": intent}
    adapter = str(metadata.get("adapter") or artifact.get("adapter") or "file")
    summary_parts = [f"{adapter.upper()} extracted"]
    for key in (
        "paragraph_count",
        "sheet_count",
        "page_count",
        "entity_count",
        "file_count",
        "text_chars",
        "duration_seconds",
        "size_bytes",
    ):
        if key in metadata:
            summary_parts.append(f"{key}={metadata[key]}")
    return _store_evidence(
        artifact,
        extractor=extractor,
        claim_level="verified",
        summary=", ".join(summary_parts) + ".",
        data=data,
    )


def _extract_image(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        data = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "has_alpha": image.mode in {"RGBA", "LA"} or "transparency" in image.info,
            "exif_orientation": image.getexif().get(274) if hasattr(image, "getexif") else None,
            "detail": detail,
            "intent": intent,
        }
    return _store_evidence(
        artifact,
        extractor="pillow",
        claim_level="verified",
        summary=f"Image metadata extracted: {data['width']}x{data['height']} {data['format']}.",
        data=data,
    )


def _extract_text(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "utf-8-replace"
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    data = {
        "encoding": encoding,
        "line_count": len(lines),
        "char_count": len(text),
        "preview": text[:4000],
        "detail": detail,
        "intent": intent,
    }
    return _store_evidence(
        artifact,
        extractor="text_adapter",
        claim_level="verified",
        summary=f"Text extracted: {len(lines)} lines, {len(text)} characters.",
        data=data,
    )


def _extract_pdf(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf

    doc = pymupdf.open(str(path))
    try:
        page_sizes = []
        text_chars = 0
        first_page_text = ""
        table_counts = []
        for page_index, page in enumerate(doc):
            rect = page.rect
            page_sizes.append({"page": page_index + 1, "width": rect.width, "height": rect.height})
            page_text = page.get_text() or ""
            text_chars += len(page_text)
            if page_index == 0:
                first_page_text = page_text[:4000]
            if hasattr(page, "find_tables"):
                try:
                    tables = page.find_tables()
                    table_counts.append({"page": page_index + 1, "tables": len(tables.tables)})
                except Exception:
                    table_counts.append({"page": page_index + 1, "tables": None})
        data = {
            "page_count": doc.page_count,
            "page_sizes": page_sizes,
            "text_chars": text_chars,
            "first_page_text_preview": first_page_text,
            "table_counts": table_counts,
            "scanned_or_image_only": text_chars == 0,
            "detail": detail,
            "intent": intent,
        }
    finally:
        doc.close()
    return _store_evidence(
        artifact,
        extractor="pymupdf",
        claim_level="verified",
        summary=f"PDF extracted: {data['page_count']} pages, {data['text_chars']} text characters.",
        data=data,
    )


def _extract_dxf(artifact: dict[str, Any], path: Path, detail: str, intent: str) -> dict[str, Any]:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    from collections import Counter
    entity_counts = Counter(entity.dxftype() for entity in msp)
    data = {
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS", 0),
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [block.name for block in doc.blocks],
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "detail": detail,
        "intent": intent,
    }
    return _store_evidence(
        artifact,
        extractor="ezdxf",
        claim_level="verified",
        summary=f"DXF extracted: {len(data['layers'])} layers, {sum(entity_counts.values())} modelspace entities.",
        data=data,
    )


def _coerce_source_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        ids = [value]
    elif isinstance(value, list):
        ids = [str(item) for item in value]
    else:
        raise ValueError("source_artifact_ids must be a non-empty list")
    if not ids:
        raise ValueError("source_artifact_ids must be non-empty")
    if len(ids) > MAX_SOURCE_ARTIFACTS:
        raise ValueError(f"source_artifact_ids supports at most {MAX_SOURCE_ARTIFACTS} source artifacts")
    return ids
