"""``pre_tool_call`` hook: stale-ID + mixed-family + over-budget enforcement.

Three predicates fire before every tool dispatch — the substrate backstop for
rules documented in ``botji-render-mode`` / ``botji-render-router`` /
``botji-codex-engineering`` skills.

1. Stale legacy source: call ``current_turn_id`` != registered ``art_*``
   record turn. Either missing turn id = indeterminate (allow). Native
   ``src_*`` cannot go stale by construction.
2. Mixed ID family: args carry BOTH ``art_*`` AND any of
   ``src_*``/``out_*``/``rcpt_*``. Complements per-tool check at
   ``botji-artifacts/_handlers.py::_reject_hermes_native_ids``.
3. Over-budget transform: third ``artifact_transform(operation="edit_image")``
   in one turn. Cap=2 from ``botji-render-mode`` Retry budget.

Rule-3 override (skill-forwarded; hook does not infer from brief text):
``user_authorized_variants: N`` (int >= 1) OR ``user_authorized_retry: true``.

Fail-open: any predicate exception logs and returns ``None``.
"""
from __future__ import annotations
import logging, threading
from typing import Any

logger = logging.getLogger(__name__)
_LEGACY = "art_"
_NATIVE = ("src_", "out_", "rcpt_")
_ID_KEYS = ("artifact_id", "source_artifact_id", "source_artifact_ids",
            "source_id", "source_ids", "output_id", "output_artifact_id",
            "parent_id", "parents", "receipt_id", "artifact_ids")
_BUDGET = 2
_counts: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def _ids(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in _ID_KEYS:
        v = args.get(key)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(str(i) for i in v if isinstance(i, str))
    return out


def _block(msg: str) -> dict[str, str]:
    return {"action": "block", "message": msg}


def _check_mixed(args: dict[str, Any]) -> dict[str, str] | None:
    ids = _ids(args)
    legacy = [i for i in ids if i.startswith(_LEGACY)]
    native = [i for i in ids if i.startswith(_NATIVE)]
    if legacy and native:
        return _block(f"Mixed-ID-family: legacy {legacy[:3]} + native {native[:3]}. "
                      "Use one pipeline — source_register for src_* or legacy artifact_* tools end-to-end.")
    return None


def _check_stale(args: dict[str, Any]) -> dict[str, str] | None:
    call_turn = str(args.get("current_turn_id") or "").strip()
    if not call_turn:
        return None
    legacy = [i for i in _ids(args) if i.startswith(_LEGACY)]
    if not legacy:
        return None
    try:
        from .. import _find_legacy_artifact  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    stale = [i for i in legacy
             if (rec := _find_legacy_artifact(i)) is not None
             and (rt := str(rec.get("current_turn_id") or "").strip())
             and rt != call_turn]
    if stale:
        return _block(f"Stale source ID: {stale[:3]} registered in a different turn "
                      f"than call (current_turn_id={call_turn!r}). Re-register the "
                      "current attachment with source_register.")
    return None


def _authorized(args: dict[str, Any]) -> bool:
    v = args.get("user_authorized_variants")
    if (isinstance(v, int) and v >= 1) or (isinstance(v, str) and v.isdigit() and int(v) >= 1):
        return True
    r = args.get("user_authorized_retry")
    return (isinstance(r, bool) and r) or (isinstance(r, str) and r.strip().lower() in {"true", "1", "yes"})


def _check_budget(tool: str, args: dict[str, Any], sid: str) -> dict[str, str] | None:
    if tool != "artifact_transform" or str(args.get("operation") or "").strip().lower() != "edit_image":
        return None
    turn = str(args.get("current_turn_id") or "").strip()
    if not turn:
        return None
    key = (str(sid or ""), turn)
    authd = _authorized(args)
    with _lock:
        n = _counts.get(key, 0)
        if not authd and n >= _BUDGET:
            return _block(f"Transform budget exhausted: {n} edit_image already in "
                          f"turn {turn!r} (cap: {_BUDGET}). Stop and ask the user. "
                          "Override with user_authorized_variants=N or user_authorized_retry=true.")
        # Record spend on authorized too so unauthorized retries cannot
        # piggyback off prior authorization.
        _counts[key] = n + 1
    return None


def _reset_transform_counts() -> None:
    """Test-only; production never calls this."""
    with _lock:
        _counts.clear()


def pre_tool_call(tool_name: str, args: dict[str, Any] | None = None,
                  session_id: str = "", **_: Any) -> dict[str, str] | None:
    """First predicate to block wins. Exceptions fail-OPEN (log + allow)."""
    try:
        a = args if isinstance(args, dict) else {}
        return _check_mixed(a) or _check_stale(a) or _check_budget(tool_name, a, session_id)
    except Exception as exc:  # noqa: BLE001 — fail-open is the policy.
        logger.warning("botji-core pre_tool_call hook failed open: %s", exc)
        return None
