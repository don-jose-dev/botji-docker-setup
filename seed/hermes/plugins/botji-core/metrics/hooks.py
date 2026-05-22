"""pre/post_tool_call hooks translating Hermes events into Prometheus updates.

Reads tool name, args, result JSON, elapsed time only — never message text,
user IDs, or file contents. Fail-open: hooks swallow exceptions.

The post_tool_call hook also appends a JSONL audit record per substrate call
to `${HERMES_HOME}/audit/<tenant>-substrate.jsonl`. Schema and redaction
policy live in `docs/OBSERVABILITY.md` ("Audit log" section). Audit failures
never propagate; the underlying tool call result reaches the caller unchanged.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import exporter

logger = logging.getLogger(__name__)

_AUDIT_TOOLS = frozenset({
    "artifact_register", "source_register", "artifact_write",
    "receipt_record", "delivery_gate", "artifact_transform", "artifact_review",
})
_KNOWN_OPS = frozenset({"edit_image", "exact_copy", "render_schema"})
_TIMED_TOOLS = _AUDIT_TOOLS  # All audited tools get pre/post wall-clock timing.
_START_TIMES: dict[tuple[str, str], float] = {}
_AUDIT_DONE: set[tuple[str, str]] = set()
_LOCK = threading.Lock()
_SECRET_RE = re.compile(
    r"(tok_[A-Za-z0-9_-]+|sk_[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|eyJ[A-Za-z0-9._-]+)")


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


def _redact(s: str) -> str:
    return _SECRET_RE.sub("<redacted:secret>", s)


def _audit_path() -> Path:
    p = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "audit" / f"{exporter.TENANT}-substrate.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _audit_write(tool_name: str, args: Any, parsed: Any, result: Any,
                 tool_call_id: str, session_id: str, duration_ms: int) -> None:
    if tool_name not in _AUDIT_TOOLS:
        return
    key = (tool_call_id or "", tool_name)
    with _LOCK:
        if key in _AUDIT_DONE:
            return
        _AUDIT_DONE.add(key)
    args_dict = args if isinstance(args, dict) else {}
    args_kinds = {str(k): type(v).__name__ for k, v in args_dict.items()}
    try:
        args_repr = json.dumps(args_dict, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_repr = repr(args_dict)
    args_hash = hashlib.sha256(args_repr.encode("utf-8")).hexdigest()
    status = "error" if isinstance(parsed, dict) and parsed.get("success") is False else "success"
    summary_src = json.dumps(parsed, default=str)[:600] if isinstance(parsed, dict) else str(result or "")[:600]
    record = {
        "timestamp": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tool": tool_name, "tenant": exporter.TENANT, "session_id": session_id or "",
        "args_hash": args_hash, "args_kinds": args_kinds, "result_status": status,
        "result_summary": _redact(summary_src)[:200], "duration_ms": int(duration_ms),
    }
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(_audit_path()), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def on_post_tool_call(*, tool_name: str = "", args: Any = None, result: Any = None,
                      tool_call_id: str = "", session_id: str = "", **_: Any) -> None:
    if not exporter._started:
        return
    parsed = _parse(result)
    with _LOCK:
        started = _START_TIMES.pop((tool_call_id or "", tool_name), None)
    duration_ms = int(max(0.0, time.monotonic() - started) * 1000) if started is not None else 0
    try:
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
            if started is not None:
                exporter.artifact_transform_duration_seconds.labels(
                    operation=op, tenant=exporter.TENANT,
                ).observe(duration_ms / 1000.0)
        if parsed is not None and parsed.get("success") is False:
            err = str(parsed.get("error") or "").strip()
            etype = (err.split(":", 1)[0] or "error")[:64] or "error"
            exporter.tool_error_total.labels(
                tool=tool_name or "unknown", error_type=etype, tenant=exporter.TENANT).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("metrics post_tool_call swallowed: %s", exc)
    try:
        _audit_write(tool_name, args, parsed, result, tool_call_id, session_id, duration_ms)
    except Exception as exc:  # noqa: BLE001 — audit must never break callers.
        logger.debug("audit post_tool_call swallowed: %s", exc)


def register_hooks(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
