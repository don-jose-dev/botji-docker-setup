"""Read-only access to botji-core's JSONL index.

botji-guards lives in a separate plugin from botji-core (sibling, not child),
so it cannot ``from .. import _read_records``. Instead it reads the same
``${BOTJI_CORE_ROOT}/index/<kind>.jsonl`` files directly. Write path stays
exclusively in botji-core; this module is read-only.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _core_root() -> Path:
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    return Path(os.environ.get("BOTJI_CORE_ROOT", str(home / "botji-core"))).resolve()


def read_records(kind: str) -> list[dict[str, Any]]:
    path = _core_root() / "index" / f"{kind}.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("botji-guards: skipped invalid JSONL line in %s", path)
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records
