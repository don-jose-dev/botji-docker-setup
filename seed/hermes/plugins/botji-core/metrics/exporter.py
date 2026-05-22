"""Prometheus exporter. Cardinality: verdict 3, operation 4, status 4,
tenant small, tool bounded by registry, error_type bounded. Never add
session/user/path/text labels.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

TENANT: str = os.environ.get("BOTJI_TENANT_ID", "unknown").strip() or "unknown"
# Aligned with budget config: tool calls cap ~90s, response ~180s.
DURATION_BUCKETS = (1.0, 5.0, 10.0, 30.0, 60.0, 90.0, 120.0, 180.0, 300.0)
DEFAULT_PORT = int(os.environ.get("BOTJI_METRICS_PORT", "9090"))
DEFAULT_BIND = os.environ.get("BOTJI_METRICS_BIND", "127.0.0.1")

delivery_gate_total: Any = None
artifact_transform_total: Any = None
artifact_transform_duration_seconds: Any = None
receipt_record_total: Any = None
tool_error_total: Any = None
container_info: Any = None
_started: bool = False


def _build(c: Any) -> None:
    global delivery_gate_total, artifact_transform_total, artifact_transform_duration_seconds
    global receipt_record_total, tool_error_total, container_info
    delivery_gate_total = c.Counter(
        "botji_delivery_gate_total", "Delivery gate verdicts.", ["verdict", "tenant"])
    artifact_transform_total = c.Counter(
        "botji_artifact_transform_total", "artifact_transform calls.", ["operation", "tenant"])
    artifact_transform_duration_seconds = c.Histogram(
        "botji_artifact_transform_duration_seconds", "artifact_transform call duration.",
        ["operation", "tenant"], buckets=DURATION_BUCKETS)
    receipt_record_total = c.Counter(
        "botji_receipt_record_total", "receipt_record calls.", ["status", "tenant"])
    tool_error_total = c.Counter(
        "botji_tool_error_total", "Tool call errors.", ["tool", "error_type", "tenant"])
    container_info = c.Gauge(
        "botji_container_info", "Container metadata (always 1).",
        ["tenant", "image_digest", "started_at"])
    started_at = _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    container_info.labels(
        tenant=TENANT,
        image_digest=os.environ.get("BOTJI_IMAGE_DIGEST", "unknown"),
        started_at=started_at).set(1)


def start_exporter(port: int | None = None, bind: str | None = None) -> bool:
    """Start the Prometheus HTTP server. Idempotent and fail-open."""
    global _started
    if _started:
        return True
    try:
        import prometheus_client  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("botji-core metrics: prometheus_client missing — disabled")
        return False
    try:
        _build(prometheus_client)
        prometheus_client.start_http_server(
            port if port is not None else DEFAULT_PORT,
            addr=bind if bind is not None else DEFAULT_BIND)
        _started = True
        logger.info("botji-core metrics: exporter on %s:%s (tenant=%s)",
                    bind or DEFAULT_BIND, port or DEFAULT_PORT, TENANT)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open contract
        logger.warning("botji-core metrics: failed to start exporter: %s", exc)
        return False
