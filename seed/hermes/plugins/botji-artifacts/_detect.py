"""File type detection — MIME, adapter, and structure sniffing."""
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
    IMAGE_SUFFIXES, TEXT_SUFFIXES, PDF_SUFFIXES, DXF_SUFFIXES, DOCX_SUFFIXES,
    XLSX_SUFFIXES, HTML_SUFFIXES, SVG_SUFFIXES, STEP_SUFFIXES, IFC_SUFFIXES,
    ZIP_SUFFIXES, AUDIO_SUFFIXES, VIDEO_SUFFIXES,
)
from _utils import _read_head, _magic_mime


def _detect_type(path: Path, declared_type: str = "auto") -> tuple[str, str]:
    suffix = path.suffix.lower()
    head = _read_head(path)
    mime = _magic_mime(path)
    declared = str(declared_type or "auto").lower()

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "image/webp", "image"
    if head.startswith(b"RIFF") and b"WAVE" in head[:16]:
        return "audio/wav", "audio"
    if head.startswith(b"%PDF"):
        return "application/pdf", "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        zip_adapter = _detect_zip_adapter(path, suffix)
        if zip_adapter == "docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
        if zip_adapter == "xlsx":
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        return "application/zip", "zip"
    if suffix in DXF_SUFFIXES or (b"SECTION" in head[:2048] and b"ENTITIES" in head[:8192]):
        return "image/vnd.dxf", "dxf"
    if suffix in IFC_SUFFIXES or _looks_ifc(head):
        return "application/x-step", "ifc"
    if suffix in STEP_SUFFIXES or _looks_step(head):
        return "model/step", "step"
    if suffix in SVG_SUFFIXES or _looks_svg(head):
        return "image/svg+xml", "svg"
    if suffix in HTML_SUFFIXES or _looks_html(head):
        return "text/html", "html"

    if mime and mime not in {"application/octet-stream", "text/plain"}:
        if mime.startswith("image/"):
            return mime, "image"
        if mime == "application/pdf":
            return mime, "pdf"
        if mime.startswith("audio/"):
            return mime, "audio"
        if mime.startswith("video/"):
            return mime, "video"
        if mime in {"text/html", "application/xhtml+xml"}:
            return mime, "html"
        if mime in {"image/svg+xml", "text/xml"} and suffix in SVG_SUFFIXES:
            return "image/svg+xml", "svg"
        if mime in {"application/zip", "application/x-zip-compressed"}:
            zip_adapter = _detect_zip_adapter(path, suffix)
            return mime, zip_adapter

    guessed, _ = mimetypes.guess_type(str(path))
    detected = mime or guessed or "application/octet-stream"
    if suffix in IMAGE_SUFFIXES or (detected and detected.startswith("image/")):
        return detected or "image/unknown", "image"
    if suffix in PDF_SUFFIXES:
        return "application/pdf", "pdf"
    if suffix in DOCX_SUFFIXES:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    if suffix in XLSX_SUFFIXES:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    if suffix in HTML_SUFFIXES:
        return "text/html", "html"
    if suffix in SVG_SUFFIXES:
        return "image/svg+xml", "svg"
    if suffix in STEP_SUFFIXES:
        return detected if detected != "application/octet-stream" else "model/step", "step"
    if suffix in IFC_SUFFIXES:
        return detected if detected != "application/octet-stream" else "application/x-step", "ifc"
    if suffix in ZIP_SUFFIXES:
        return detected if detected != "application/octet-stream" else "application/zip", "zip"
    if suffix in AUDIO_SUFFIXES or detected.startswith("audio/"):
        return detected if detected != "application/octet-stream" else "audio/unknown", "audio"
    if suffix in VIDEO_SUFFIXES or detected.startswith("video/"):
        return detected if detected != "application/octet-stream" else "video/unknown", "video"
    if suffix in TEXT_SUFFIXES or _looks_text(head):
        return detected if detected.startswith("text/") else "text/plain", "text"
    if declared in STRUCTURED_ADAPTERS:
        return detected, declared
    return detected, "binary"


def _detect_zip_adapter(path: Path, suffix: str) -> str:
    if suffix in DOCX_SUFFIXES:
        return "docx"
    if suffix in XLSX_SUFFIXES:
        return "xlsx"
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except Exception:
        return "zip"
    if "[Content_Types].xml" in names and any(name.startswith("word/") for name in names):
        return "docx"
    if "[Content_Types].xml" in names and any(name.startswith("xl/") for name in names):
        return "xlsx"
    return "zip"


def _head_text(head: bytes) -> str:
    return head.decode("utf-8", errors="ignore").lstrip("\ufeff").strip().lower()


def _looks_html(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return "<!doctype html" in text or "<html" in text or "<head" in text or "<body" in text


def _looks_svg(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return "<svg" in text or ("http://www.w3.org/2000/svg" in text and "<" in text)


def _looks_step(head: bytes) -> bool:
    text = _head_text(head[:4096])
    return text.startswith("iso-10303-21") or "header;" in text and "file_schema" in text


def _looks_ifc(head: bytes) -> bool:
    text = _head_text(head[:8192])
    return _looks_step(head) and ("ifc" in text or "ifcproject" in text or "ifcsite" in text)


def _looks_text(head: bytes) -> bool:
    if not head:
        return True
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


