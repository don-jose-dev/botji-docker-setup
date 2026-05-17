"""botji-allowlist plugin: file-backed Telegram user allowlist with hot reload.

Replaces the TELEGRAM_ALLOWED_USERS env-var approach. The env-var approach
required ``docker compose up -d --force-recreate`` to add a user (env is
baked at container-create time, not at restart). The file-backed approach
reads ``/opt/data/allowed_users.json`` on every gateway dispatch and
honours changes immediately — no restart at all.

Operators add a user by editing the JSON file (or via a future
``hermes botji allowlist add <id>`` CLI) and saving. The file is watched
via mtime polling at gateway dispatch time, which is cheap (one stat call)
and works under any filesystem including overlayfs without inotify.

The plugin uses the ``pre_gateway_dispatch`` hook to inspect every
incoming MessageEvent and return ``{"action": "skip", "reason": "..."}``
when the user isn't on the allowlist. Hermes will then drop the message
without replying — matching the legacy "Unauthorized user" behaviour but
sourced from a file instead of an env var.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)


_ALLOWLIST_PATH = Path(os.environ.get(
    "BOTJI_ALLOWLIST_PATH",
    "/opt/data/allowed_users.json",
))


class _AllowlistCache:
    """mtime-watched cache of the allowlist file.

    Re-reads the file only when its mtime changes. Survives missing file
    by treating "no file" as "no allowed users" (gateway env-var fallback
    handles bootstrap if BOTJI_FALLBACK_ENV_ALLOWLIST=1 is set).
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = RLock()
        self._allowed: set[str] = set()
        self._mtime: float = 0.0
        self._reload()

    def _reload(self) -> None:
        with self._lock:
            try:
                stat = self._path.stat()
            except FileNotFoundError:
                if self._allowed:
                    logger.warning("allowlist: file %s vanished; treating as empty", self._path)
                self._allowed = set()
                self._mtime = 0.0
                return
            if stat.st_mtime == self._mtime:
                return
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("allowlist: failed to parse %s: %s (keeping previous set)", self._path, exc)
                return
            users = data.get("users") if isinstance(data, dict) else data
            if not isinstance(users, list):
                logger.error("allowlist: %s must contain a 'users' list", self._path)
                return
            new_set = {str(u).strip() for u in users if str(u).strip()}
            if new_set != self._allowed:
                logger.info(
                    "allowlist: reloaded %d user(s) from %s (was %d)",
                    len(new_set),
                    self._path,
                    len(self._allowed),
                )
            self._allowed = new_set
            self._mtime = stat.st_mtime

    def is_allowed(self, user_id: str) -> bool:
        self._reload()
        return str(user_id) in self._allowed

    def count(self) -> int:
        self._reload()
        return len(self._allowed)


_cache: _AllowlistCache | None = None


def _cache_get() -> _AllowlistCache:
    global _cache
    if _cache is None:
        _cache = _AllowlistCache(_ALLOWLIST_PATH)
    return _cache


def _env_allowlist_fallback() -> set[str]:
    """Fallback to TELEGRAM_ALLOWED_USERS env var if the file is empty.

    Eases migration: deployments using the legacy env var still work
    until they migrate the list into the JSON file. Once the file has
    any users, the env fallback is ignored.
    """
    raw = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _pre_gateway_dispatch(event: Any, **_: Any) -> dict[str, Any] | None:
    """Block unauthorized users at the gateway boundary.

    Returns:
        ``{"action": "skip", "reason": ...}`` to drop the message silently.
        None to let normal dispatch proceed.
    """
    # The event is a MessageEvent dataclass; access defensively to avoid
    # breaking on future schema changes.
    platform = getattr(event, "platform", None) or getattr(event, "source", None)
    if platform != "telegram":
        return None  # Other platforms enforce their own allow rules.

    sender = (
        getattr(event, "sender_id", None)
        or getattr(event, "user_id", None)
        or getattr(event, "from_id", None)
    )
    if sender is None:
        # No sender ID — fall through to default auth (which may itself reject).
        return None

    cache = _cache_get()
    allowed = cache.is_allowed(sender)
    if not allowed and cache.count() == 0:
        # File empty / missing — try env fallback for bootstrap.
        if str(sender) in _env_allowlist_fallback():
            allowed = True

    if not allowed:
        sender_name = (
            getattr(event, "sender_name", None)
            or getattr(event, "user_name", None)
            or "unknown"
        )
        logger.warning(
            "allowlist: rejected telegram user %s (%s)", sender, sender_name
        )
        return {"action": "skip", "reason": f"user {sender} not on allowlist"}
    return None


def _bootstrap_file_from_env_if_missing() -> None:
    """On first run, seed the JSON file from the legacy env var.

    Only fires when the file doesn't exist *and* the env var is set —
    so existing deployments upgrade without dropping users. Subsequent
    edits go through the JSON file.
    """
    if _ALLOWLIST_PATH.exists():
        return
    env_users = _env_allowlist_fallback()
    if not env_users:
        return
    _ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "users": sorted(env_users),
        "_comment": (
            "Edit this file to add/remove users. Changes take effect on the next "
            "Telegram message (no restart needed). Each entry is a Telegram numeric ID."
        ),
    }
    _ALLOWLIST_PATH.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("allowlist: bootstrapped %s from TELEGRAM_ALLOWED_USERS (%d users)",
                _ALLOWLIST_PATH, len(env_users))


def register(ctx) -> None:
    """Plugin entry point."""
    _bootstrap_file_from_env_if_missing()
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    logger.info(
        "botji-allowlist: pre_gateway_dispatch hook registered (path=%s, users=%d)",
        _ALLOWLIST_PATH,
        _cache_get().count(),
    )
