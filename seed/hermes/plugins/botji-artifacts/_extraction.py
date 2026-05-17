"""Schema normalisation, metadata extraction for all adapters, and artifact extraction."""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import struct
import sys
import uuid
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
import wave
import zipfile
from _constants import (
    ARTIFACT_SCHEMA_VERSION, STRUCTURED_ADAPTERS, MAX_SOURCE_ARTIFACTS,
    IMAGE_SUFFIXES, TEXT_SUFFIXES, PDF_SUFFIXES, DXF_SUFFIXES,
)
from _utils import (
    _json, _now, _new_id, _artifact_root, _hermes_home, _sha256,
    _resolve_allowed_path, _safe_filename,
)
from _detect import _detect_type
from _registry import (
    _load_records, _append_record, _replace_record,
    _load_artifact, _write_json, _store_evidence,
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


def _image_metadata(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        return {
            "adapter": "image",
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "has_alpha": image.mode in {"RGBA", "LA"} or "transparency" in image.info,
            "exif_orientation": image.getexif().get(274) if hasattr(image, "getexif") else None,
        }


def _text_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "utf-8-replace"
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "adapter": "text",
        "encoding": encoding,
        "line_count": len(lines),
        "char_count": len(text),
        "preview": text[:4000],
        "line_spans": [{"line": index + 1, "chars": len(line)} for index, line in enumerate(lines[:200])],
        "truncated_line_spans": len(lines) > 200,
    }


def _pdf_metadata(path: Path) -> dict[str, Any]:
    try:
        import pymupdf
    except Exception:
        import fitz as pymupdf

    doc = pymupdf.open(str(path))
    try:
        page_sizes = []
        text_chars = 0
        first_page_text = ""
        for page_index, page in enumerate(doc):
            rect = page.rect
            page_sizes.append({"page": page_index + 1, "width": rect.width, "height": rect.height})
            page_text = page.get_text() or ""
            text_chars += len(page_text)
            if page_index == 0:
                first_page_text = page_text[:4000]
        return {
            "adapter": "pdf",
            "page_count": doc.page_count,
            "page_sizes": page_sizes,
            "text_chars": text_chars,
            "first_page_text_preview": first_page_text,
            "scanned_or_image_only": text_chars == 0,
        }
    finally:
        doc.close()


def _dxf_metadata(path: Path) -> dict[str, Any]:
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in msp)
    extents: dict[str, Any] | None = None
    try:
        bbox = msp.bbox()
        if bbox.has_data:
            extents = {
                "min": [bbox.extmin.x, bbox.extmin.y, bbox.extmin.z],
                "max": [bbox.extmax.x, bbox.extmax.y, bbox.extmax.z],
            }
    except Exception:
        extents = None
    return {
        "adapter": "dxf",
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS", 0),
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [block.name for block in doc.blocks],
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "modelspace_extents": extents,
    }


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_text(element: ET.Element) -> str:
    return "".join(text for text in element.itertext() if text)


def _zip_read_text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode("utf-8", errors="replace")


def _docx_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            document_xml = _zip_read_text(archive, "word/document.xml") if "word/document.xml" in names else ""
    except Exception as exc:
        return {"adapter": "docx", "package_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    paragraphs: list[str] = []
    table_count = 0
    if document_xml:
        try:
            root = ET.fromstring(document_xml)
            for element in root.iter():
                name = _xml_name(element.tag)
                if name == "tbl":
                    table_count += 1
                elif name == "p":
                    text = _xml_text(element).strip()
                    if text:
                        paragraphs.append(text)
        except ET.ParseError as exc:
            return {
                "adapter": "docx",
                "package_valid": True,
                "xml_valid": False,
                "parse_error": f"document.xml: {exc}",
                "entry_count": len(names),
            }
    text = "\n".join(paragraphs)
    return {
        "adapter": "docx",
        "package_valid": True,
        "xml_valid": True,
        "entry_count": len(names),
        "paragraph_count": len(paragraphs),
        "table_count": table_count,
        "text_chars": len(text),
        "text_preview": text[:4000],
        "core_entries": sorted(name for name in names if name in {"[Content_Types].xml", "word/document.xml", "docProps/core.xml", "docProps/app.xml"}),
    }


def _xlsx_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            workbook_xml = _zip_read_text(archive, "xl/workbook.xml") if "xl/workbook.xml" in names else ""
            shared_xml = _zip_read_text(archive, "xl/sharedStrings.xml") if "xl/sharedStrings.xml" in names else ""
            worksheet_names = sorted(name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml"))
            worksheets = [_xlsx_sheet_summary(archive, name) for name in worksheet_names]
    except Exception as exc:
        return {"adapter": "xlsx", "package_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    sheet_names: list[str] = []
    if workbook_xml:
        try:
            root = ET.fromstring(workbook_xml)
            sheet_names = [str(element.attrib.get("name")) for element in root.iter() if _xml_name(element.tag) == "sheet" and element.attrib.get("name")]
        except ET.ParseError:
            sheet_names = []
    shared_strings = _xlsx_shared_strings(shared_xml)
    return {
        "adapter": "xlsx",
        "package_valid": True,
        "entry_count": len(names),
        "sheet_count": len(worksheet_names),
        "sheet_names": sheet_names,
        "worksheets": worksheets,
        "shared_string_count": len(shared_strings),
        "shared_string_preview": shared_strings[:20],
    }


def _xlsx_shared_strings(shared_xml: str) -> list[str]:
    if not shared_xml:
        return []
    try:
        root = ET.fromstring(shared_xml)
    except ET.ParseError:
        return []
    strings: list[str] = []
    for item in root.iter():
        if _xml_name(item.tag) == "si":
            value = _xml_text(item).strip()
            if value:
                strings.append(value)
    return strings


def _xlsx_sheet_summary(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(_zip_read_text(archive, name))
    except Exception as exc:
        return {"path": name, "parse_error": f"{type(exc).__name__}: {exc}"}
    dimension = None
    rows = set()
    cells = []
    formulas = 0
    for element in root.iter():
        local = _xml_name(element.tag)
        if local == "dimension":
            dimension = element.attrib.get("ref")
        elif local == "row" and element.attrib.get("r"):
            rows.add(element.attrib["r"])
        elif local == "c":
            ref = element.attrib.get("r")
            if ref:
                cells.append(ref)
        elif local == "f":
            formulas += 1
    return {
        "path": name,
        "dimension": dimension,
        "row_count": len(rows),
        "cell_count": len(cells),
        "formula_count": formulas,
        "cell_refs_preview": cells[:50],
    }


class _HTMLSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag_counts: Counter[str] = Counter()
        self.links: list[str] = []
        self.images: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag_counts[tag.lower()] += 1
        attr_map = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])
        if tag.lower() == "img" and attr_map.get("src"):
            self.images.append(attr_map["src"])
        if tag.lower() in {"title", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._capture = tag.lower()

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag.lower():
            self._capture = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        self.text_parts.append(text)
        if self._capture == "title":
            self.title_parts.append(text)
        elif self._capture and self._capture.startswith("h"):
            self.headings.append({"level": self._capture, "text": text})


def _html_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    parser = _HTMLSummaryParser()
    parser.feed(text)
    body_text = " ".join(parser.text_parts)
    return {
        "adapter": "html",
        "encoding": "utf-8-replace" if b"\xef\xbf\xbd" in raw else "utf-8",
        "title": " ".join(parser.title_parts)[:500],
        "tag_counts": dict(sorted(parser.tag_counts.items())),
        "heading_count": len(parser.headings),
        "headings": parser.headings[:50],
        "link_count": len(parser.links),
        "image_count": len(parser.images),
        "links_preview": parser.links[:50],
        "images_preview": parser.images[:50],
        "text_chars": len(body_text),
        "text_preview": body_text[:4000],
    }


def _svg_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"adapter": "svg", "xml_valid": False, "parse_error": str(exc), "text_preview": text[:4000]}
    element_counts: Counter[str] = Counter(_xml_name(element.tag) for element in root.iter())
    text_items = [" ".join(_xml_text(element).split()) for element in root.iter() if _xml_name(element.tag) == "text"]
    text_items = [item for item in text_items if item]
    return {
        "adapter": "svg",
        "xml_valid": True,
        "root_tag": _xml_name(root.tag),
        "width": root.attrib.get("width"),
        "height": root.attrib.get("height"),
        "viewBox": root.attrib.get("viewBox"),
        "element_counts": dict(sorted(element_counts.items())),
        "text_items": text_items[:100],
    }


def _step_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entity_counts = _step_entity_counts(text)
    return {
        "adapter": "step",
        "schema": _step_schema(text),
        "entity_count": sum(entity_counts.values()),
        "entity_counts": entity_counts,
        "header_preview": _step_header_preview(text),
    }


def _ifc_metadata(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    entity_counts = _step_entity_counts(text)
    hierarchy_keys = ["IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE", "IFCWALL", "IFCDOOR", "IFCWINDOW"]
    return {
        "adapter": "ifc",
        "schema": _step_schema(text),
        "entity_count": sum(entity_counts.values()),
        "entity_counts": entity_counts,
        "spatial_hierarchy_counts": {key: entity_counts.get(key, 0) for key in hierarchy_keys},
        "header_preview": _step_header_preview(text),
    }


def _step_entity_counts(text: str) -> dict[str, int]:
    counts = Counter(match.group(1).upper() for match in re.finditer(r"#\d+\s*=\s*([A-Z0-9_]+)\s*\(", text.upper()))
    return dict(sorted(counts.items()))


def _step_schema(text: str) -> str | None:
    match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _step_header_preview(text: str) -> str:
    upper = text.upper()
    end = upper.find("ENDSEC;")
    return text[: end + len("ENDSEC;") if end != -1 else 1000]


def _zip_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            files = [entry for entry in entries if not entry.is_dir()]
            dirs = [entry for entry in entries if entry.is_dir()]
            preview = [
                {
                    "name": entry.filename,
                    "size": entry.file_size,
                    "compressed_size": entry.compress_size,
                    "crc": f"{entry.CRC:08x}",
                }
                for entry in files[:200]
            ]
    except Exception as exc:
        return {"adapter": "zip", "archive_valid": False, "parse_error": f"{type(exc).__name__}: {exc}"}
    return {
        "adapter": "zip",
        "archive_valid": True,
        "entry_count": len(entries),
        "file_count": len(files),
        "directory_count": len(dirs),
        "total_uncompressed_bytes": sum(entry.file_size for entry in files),
        "entries_preview": preview,
    }


def _audio_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        try:
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                rate = audio.getframerate()
                return {
                    "adapter": "audio",
                    "container": "wav",
                    "channels": audio.getnchannels(),
                    "sample_width_bytes": audio.getsampwidth(),
                    "sample_rate": rate,
                    "frame_count": frames,
                    "duration_seconds": round(frames / rate, 6) if rate else None,
                }
        except Exception as exc:
            return {"adapter": "audio", "container": "wav", "parse_error": f"{type(exc).__name__}: {exc}", **_byte_signature(path)}
    return {"adapter": "audio", "container": suffix.lstrip(".") or "unknown", **_byte_signature(path)}


def _video_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    data = _read_head(path, 1024 * 1024)
    boxes = _mp4_box_summary(data) if suffix in {".mp4", ".m4v", ".mov"} or b"ftyp" in data[:32] else []
    return {
        "adapter": "video",
        "container": suffix.lstrip(".") or "unknown",
        "box_counts": dict(sorted(Counter(box["type"] for box in boxes).items())),
        "boxes_preview": boxes[:50],
        **_byte_signature(path),
    }


def _binary_metadata(path: Path) -> dict[str, Any]:
    return {"adapter": "binary", **_byte_signature(path)}


def _byte_signature(path: Path) -> dict[str, Any]:
    head = _read_head(path, 64)
    return {
        "size_bytes": path.stat().st_size,
        "head_hex": head.hex(),
        "head_ascii": "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in head),
    }


def _mp4_box_summary(data: bytes) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    offset = 0
    while offset + 8 <= len(data) and len(boxes) < 200:
        size, raw_type = struct.unpack(">I4s", data[offset : offset + 8])
        box_type = raw_type.decode("ascii", errors="replace")
        header = 8
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            break
        boxes.append({"type": box_type, "offset": offset, "size": size})
        offset += size
    return boxes


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

