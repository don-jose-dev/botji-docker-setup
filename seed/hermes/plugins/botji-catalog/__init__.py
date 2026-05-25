"""botji-catalog: per-tenant material vocabulary lock.

Tools: catalog_check / catalog_load / catalog_update. Semantic plugin
(vocabulary) — lives outside botji-core per the charter. Catalog file is
``/opt/data/catalog.json`` (override via ``BOTJI_CATALOG_PATH``). Malformed
JSON falls back to the shipped default; the plugin never crashes the harness.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_FILE = Path(__file__).resolve().parent / "_default_catalog.json"
_ALLOWED_FIELDS = {"stones", "woods", "metals", "paints", "fixtures"}
_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"path": None, "mtime": None, "data": None}


def _catalog_path() -> Path:
    return Path(os.environ.get("BOTJI_CATALOG_PATH", "/opt/data/catalog.json")).expanduser()


def _load_default() -> dict[str, Any]:
    try:
        return json.loads(_DEFAULT_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("botji-catalog: default catalog unreadable (%s)", exc)
        return {"version": 1, "tenant_id": "unknown", "materials": {},
                "banned_terms": [], "substitutions": {}}


def _read_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("botji-catalog: %s unreadable/malformed (%s) -- falling back", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("botji-catalog: %s is not a JSON object -- falling back", path)
        return None
    return data


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    materials_raw = data.get("materials") if isinstance(data.get("materials"), dict) else {}
    materials: dict[str, list[str]] = {}
    for field, values in materials_raw.items():
        if isinstance(values, list):
            materials[str(field)] = [str(v) for v in values if isinstance(v, (str, int, float))]
    banned = data.get("banned_terms") if isinstance(data.get("banned_terms"), list) else []
    subs = data.get("substitutions") if isinstance(data.get("substitutions"), dict) else {}
    return {
        "version": data.get("version", 1),
        "tenant_id": str(data.get("tenant_id") or "unknown"),
        "updated_at": data.get("updated_at"),
        "materials": materials,
        "banned_terms": [str(b) for b in banned],
        "substitutions": {str(k): str(v) for k, v in subs.items()},
    }


def catalog_load() -> dict[str, Any]:
    """Return the active catalog dict, hot-reloading on mtime change."""
    path = _catalog_path()
    with _LOCK:
        if not path.exists():
            if _CACHE["path"] != "__default__":
                logger.info("botji-catalog: %s missing -- using shipped default", path)
                _CACHE.update(path="__default__", mtime=None, data=_normalize(_load_default()))
            return dict(_CACHE["data"])
        mtime = path.stat().st_mtime
        if _CACHE["path"] != str(path) or _CACHE["mtime"] != mtime:
            _CACHE.update(path=str(path), mtime=mtime,
                          data=_normalize(_read_file(path) or _load_default()))
        return dict(_CACHE["data"])


def _word(term: str, lowered: str) -> bool:
    return re.search(rf"\b{re.escape(term.lower())}\b", lowered) is not None


def catalog_check(brief: str) -> dict[str, Any]:
    """Score a brief against the active catalog."""
    if not isinstance(brief, str):
        return {"status": "block", "banned_hits": [], "suggested_substitutions": {},
                "catalog_terms_used": [], "error": "brief must be a string"}
    cat = catalog_load()
    lowered = brief.lower()
    all_terms = [t for vals in cat.get("materials", {}).values() for t in vals]
    banned = sorted({b for b in cat.get("banned_terms", []) if _word(b, lowered)})
    used = sorted({t for t in all_terms if t and t.lower() in lowered})
    used_lower = {t.lower() for t in used}
    suggested = {g: r for g, r in cat.get("substitutions", {}).items()
                 if _word(g, lowered) and r.lower() not in used_lower}
    status = "block" if banned else "warn" if suggested else "pass"
    return {"status": status, "banned_hits": banned,
            "suggested_substitutions": suggested, "catalog_terms_used": used}


def catalog_update(field: str, value: list) -> dict[str, Any]:
    """Append entries to ``materials.<field>`` and persist to disk."""
    field = str(field or "").strip()
    if field not in _ALLOWED_FIELDS:
        return {"success": False, "error": f"field must be one of {sorted(_ALLOWED_FIELDS)}"}
    if not isinstance(value, list) or not value:
        return {"success": False, "error": "value must be a non-empty list"}
    additions = [str(v).strip() for v in value if str(v).strip()]
    if not additions:
        return {"success": False, "error": "value must contain at least one non-empty entry"}
    path = _catalog_path()
    with _LOCK:
        raw = _read_file(path) if path.exists() else None
        current = _normalize(raw if raw is not None else _load_default())
        existing = current["materials"].setdefault(field, [])
        appended = [item for item in additions if item not in existing]
        existing.extend(appended)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        except OSError as exc:
            logger.warning("botji-catalog: cannot persist to %s (%s)", path, exc)
            return {"success": False, "error": f"persist failed: {exc}"}
        _CACHE.update(path=str(path), mtime=path.stat().st_mtime, data=current)
        return {"success": True, "field": field, "appended": appended,
                "materials_field_size": len(existing), "catalog_path": str(path)}


# --- Hermes tool registration -------------------------------------------

def _wrap(name: str, fn, err: dict):
    def _handler(args: dict[str, Any], **_: Any) -> str:
        try:
            result = fn(args or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("botji-catalog: %s failed", name)
            result = {**err, "error": str(exc)}
        return json.dumps(result, ensure_ascii=False, default=str)
    return _handler


_TOOLS = [
    ("catalog_check",
     "Score a render brief against the per-tenant catalog. Returns pass/warn/block plus banned hits and suggested substitutions.",
     {"type": "object", "properties": {"brief": {"type": "string"}}, "required": ["brief"]},
     _wrap("catalog_check", lambda a: catalog_check(str(a.get("brief") or "")),
           {"status": "block"})),
    ("catalog_load",
     "Return the active per-tenant catalog dict (cached, mtime hot-reloaded).",
     {"type": "object", "properties": {}, "required": []},
     _wrap("catalog_load", lambda _a: {"success": True, "catalog": catalog_load()},
           {"success": False})),
    ("catalog_update",
     "Append entries to materials.<field> in the catalog and persist to disk.",
     {"type": "object",
      "properties": {"field": {"type": "string", "enum": sorted(_ALLOWED_FIELDS)},
                     "value": {"type": "array", "items": {"type": "string"}}},
      "required": ["field", "value"]},
     _wrap("catalog_update",
           lambda a: catalog_update(str(a.get("field") or ""), a.get("value") or []),
           {"success": False})),
]


def register(ctx) -> None:
    for name, desc, params, handler in _TOOLS:
        ctx.register_tool(name=name, toolset="text", description=desc,
                          schema={"name": name, "description": desc, "parameters": params},
                          handler=handler)
    logger.info("botji-catalog: registered (catalog_path=%s)", _catalog_path())


__all__ = ["catalog_check", "catalog_load", "catalog_update", "register"]
