"""Path resolution, ID generation, and file utility helpers."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any
from _constants import SECRET_PARTS, SECRET_FILE_NAMES, SECRET_KEYWORDS


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()


def _workspace_root() -> Path:
    return Path(os.environ.get("BOTJI_WORKDIR", "/workspace")).resolve()


def _artifact_root() -> Path:
    return Path(os.environ.get("BOTJI_ARTIFACT_ROOT", str(_hermes_home() / "artifacts"))).resolve()


def _max_bytes() -> int:
    raw = os.environ.get("BOTJI_ARTIFACT_MAX_BYTES", "104857600")
    try:
        return max(1, int(raw))
    except ValueError:
        return 104857600


def _index_path() -> Path:
    return _artifact_root() / "index" / "artifacts.jsonl"


def _safe_roots() -> list[Path]:
    return [_hermes_home(), _workspace_root()]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_secret(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    name = path.name.lower()
    if any(part in SECRET_PARTS for part in parts):
        return True
    if name in SECRET_FILE_NAMES:
        return True
    return any(keyword in name for keyword in SECRET_KEYWORDS)


def _resolve_allowed_path(raw: Any, *, field: str, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} must be a non-empty path string")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = _hermes_home() / candidate
    path = candidate.resolve(strict=must_exist)
    if not any(_is_relative_to(path, root) for root in _safe_roots()):
        allowed = ", ".join(str(root) for root in _safe_roots())
        raise ValueError(f"{field} must be under one of: {allowed}")
    if _looks_secret(path):
        raise ValueError(f"{field} points at a protected credential-like path")
    if must_exist and not path.is_file():
        raise ValueError(f"{field} is not a file: {path}")
    return path


def _safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in name)
    return cleaned[:120] or "artifact.bin"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_head(path: Path, size: int = 8192) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _magic_mime(path: Path) -> str | None:
    try:
        import magic

        return magic.from_file(str(path), mime=True)
    except Exception:
        return None


