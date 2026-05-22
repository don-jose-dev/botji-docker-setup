"""pre/post_tool_call hooks translating Hermes events into Prometheus updates.

Reads tool name, args, result JSON, elapsed time only — never message text,
user IDs, or file contents. Fail-open: hooks swallow exceptions.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from . import exporter

logger = logging.getLogger(__name__)

_TIMED_TOOLS = frozenset({"artifact_transform"})
_KNOWN_OPS = frozenset({"edit_image", "exact_copy", "render_schema"})
_START_TIMES: dict[tuple[str, str], float] = {}
_LOCK = threading.Lock()


def _parse(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return None
    try:
        p = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return None
    return p if isinstance(p, dict) else None


def _operation(args: Any) -> str:
    if not isinstance(args, dict):
        return "other"
    raw = str(args.get("operation") or "").strip()
    return raw if raw in _KNOWN_OPS else "other"


def _verdict(raw: str) -> str:
    raw = raw.strip().lower()
    if raw == "clear":
        return "clear"
    if raw in {"warn", "warned"}:
        return "warn"
    if raw in {"block", "blocked"}:
        return "block"
    return "unknown"


def on_pre_tool_call(*, tool_name: str = "", tool_call_id: str = "", **_: Any) -> None:
    if not exporter._started or tool_name not in _TIMED_TOOLS:
        return
    try:
        with _LOCK:
            _START_TIMES[(tool_call_id or "", tool_name)] = time.monotonic()
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics pre_tool_call swallowed: %s", exc)


def on_post_tool_call(*, tool_name: str = "", args: Any = None, result: Any = None,
                      tool_call_id: str = "", **_: Any) -> None:
    if not exporter._started:
        return
    try:
        parsed = _parse(result)
        # Only emit verdict/status metrics when the tool actually succeeded —
        # a failed call still gets counted in tool_error_total below.
        ok = parsed is not None and parsed.get("success") is not False
        if tool_name == "delivery_gate" and parsed is not None and ok:
            exporter.delivery_gate_total.labels(
                verdict=_verdict(str(parsed.get("delivery_gate") or "")),
                tenant=exporter.TENANT).inc()
        elif tool_name == "receipt_record" and parsed is not None and ok:
            receipt = parsed.get("receipt") if isinstance(parsed.get("receipt"), dict) else {}
            s = str(receipt.get("status") or "").strip().lower()
            status = s if s in {"pass", "warn", "block"} else "unknown"
            exporter.receipt_record_total.labels(status=status, tenant=exporter.TENANT).inc()
        elif tool_name == "artifact_transform":
            op = _operation(args)
            exporter.artifact_transform_total.labels(operation=op, tenant=exporter.TENANT).inc()
            with _LOCK:
                started = _START_TIMES.pop((tool_call_id or "", tool_name), None)
            if started is not None:
                exporter.artifact_transform_duration_seconds.labels(
                    operation=op, tenant=exporter.TENANT,
                ).observe(max(0.0, time.monotonic() - started))
        if parsed is not None and parsed.get("success") is False:
            err = str(parsed.get("error") or "").strip()
            etype = (err.split(":", 1)[0] or "error")[:64] or "error"
            exporter.tool_error_total.labels(
                tool=tool_name or "unknown", error_type=etype, tenant=exporter.TENANT).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics post_tool_call swallowed: %s", exc)


def register_hooks(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
