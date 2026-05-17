"""Module-level constants shared across all botji-artifacts submodules."""
from __future__ import annotations
import os

API_MODEL = os.environ.get("BOTJI_IMAGE_MODEL", "chatgpt-image-latest")
VISION_REVIEW_MODEL = os.environ.get("BOTJI_VISION_REVIEW_MODEL", "gpt-5.4-mini")
CODEX_CHAT_MODEL = os.environ.get("BOTJI_CODEX_IMAGE_CHAT_MODEL", "gpt-5.4-mini")
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
MAX_SOURCE_ARTIFACTS = 16
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".css", ".js", ".ts", ".py"}
PDF_SUFFIXES = {".pdf"}
DXF_SUFFIXES = {".dxf"}
DOCX_SUFFIXES = {".docx"}
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
HTML_SUFFIXES = {".html", ".htm"}
SVG_SUFFIXES = {".svg"}
STEP_SUFFIXES = {".step", ".stp"}
IFC_SUFFIXES = {".ifc"}
ZIP_SUFFIXES = {".zip"}
AUDIO_SUFFIXES = {".wav", ".wave", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
STRUCTURED_ADAPTERS = {
    "image",
    "text",
    "pdf",
    "dxf",
    "docx",
    "xlsx",
    "html",
    "svg",
    "step",
    "ifc",
    "zip",
    "audio",
    "video",
    "binary",
}
SECRET_PARTS = {".codex", ".ssh", ".gnupg", ".config"}
SECRET_FILE_NAMES = {".env", "auth.json", "credentials.json", "id_rsa", "id_ed25519"}
SECRET_KEYWORDS = ("secret", "token", "credential", "password", "api_key", "apikey")
CORE_REVIEW_AXES = [
    "source_coverage",
    "authority_alignment",
    "preserve_change",
    "groundedness",
    "uncertainty",
    "safety",
    "artifact_lineage",
    "actionability",
]

ARTIFACT_SCHEMA_VERSION = "botji.artifact_schema.v1"
