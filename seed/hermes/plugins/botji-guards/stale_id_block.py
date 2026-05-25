"""``pre_tool_call`` hook: mixed-family + over-budget enforcement.

Two predicates fire before every tool dispatch — the substrate backstop for
rules documented in ``botji-render-mode`` / ``botji-render-router`` /
``botji-codex-engineering`` skills.

1. Mixed ID family: args carry BOTH ``art_*`` AND any of
   ``src_*``/``out_*``/``rcpt_*``. Generic prefix check on tool arguments —
   the legacy plugin is gone, but defense-in-depth catches any stale art_
   leaking from older receipt history or skill prose drift.
2. Over-budget transform: third ``operation_run(operation="edit_image")``
   in one turn. Cap=2 from ``botji-render-mode`` Retry budget.

Rule-2 override (skill-forwarded; hook does not infer from brief text):
``user_authorized_variants: N`` (int >= 1) OR ``user_authorized_retry: true``.

V1R PR 11 removed the stale-legacy predicate that depended on the
``botji-artifacts`` index. With that plugin deleted, the legacy index is
permanently gone and the predicate had no work to do. PR 11 also moved this
hook out of botji-core into the sibling ``botji-guards`` plugin so the
substrate stays pure state + tools.

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
                      "Use one pipeline — source_register for src_* end-to-end. "
                      "Legacy art_* tools were removed in V1R PR 11.")
    return None


def _authorized(args: dict[str, Any]) -> bool:
    v = args.get("user_authorized_variants")
    if (isinstance(v, int) and v >= 1) or (isinstance(v, str) and v.isdigit() and int(v) >= 1):
        return True
    r = args.get("user_authorized_retry")
    return (isinstance(r, bool) and r) or (isinstance(r, str) and r.strip().lower() in {"true", "1", "yes"})


def _check_budget(tool: str, args: dict[str, Any], sid: str) -> dict[str, str] | None:
    if tool not in {"artifact_transform", "operation_run"} or str(args.get("operation") or "").strip().lower() != "edit_image":
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
        return _check_mixed(a) or _check_budget(tool_name, a, session_id)
    except Exception as exc:  # noqa: BLE001 — fail-open is the policy.
        logger.warning("botji-guards pre_tool_call hook failed open: %s", exc)
        return None
