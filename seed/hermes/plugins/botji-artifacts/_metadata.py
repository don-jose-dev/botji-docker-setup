"""Per-adapter file metadata parsing — read a file, return a dict of facts."""
from __future__ import annotations

import re
import struct
import wave
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _read_head(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_text(element: ET.Element) -> str:
    return "".join(text for text in element.itertext() if text)


def _zip_read_text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode("utf-8", errors="replace")


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


# ── Per-adapter metadata functions ────────────────────────────────────────────

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
    entity_count = sum(entity_counts.values())
    extents: dict[str, Any] | None = None
    extents_skipped = False
    if entity_count < 5_000:
        try:
            bbox = msp.bbox()
            if bbox.has_data:
                extents = {
                    "min": [bbox.extmin.x, bbox.extmin.y, bbox.extmin.z],
                    "max": [bbox.extmax.x, bbox.extmax.y, bbox.extmax.z],
                }
        except Exception:
            extents = None
    else:
        extents_skipped = True
    return {
        "adapter": "dxf",
        "dxfversion": doc.dxfversion,
        "insunits": doc.header.get("$INSUNITS", 0),
        "layers": [layer.dxf.name for layer in doc.layers],
        "blocks": [block.name for block in doc.blocks],
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "modelspace_extents": extents,
        "modelspace_extents_skipped": extents_skipped,
    }


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
