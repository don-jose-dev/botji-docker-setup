"""Botji observability sidecar.

Extracted from ``botji-core/metrics/`` in V1R PR 11 so the substrate stays
pure state + tools. Owns the Prometheus exporter + the JSONL audit log; both
fail open so a metrics/audit error never breaks substrate calls.

See ``docs/OBSERVABILITY.md`` for the metric/cardinality/audit contracts.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    try:
        from .exporter import start_exporter
        from .hooks import register_hooks
        if start_exporter():
            register_hooks(ctx)
            logger.info("botji-observability: hooks registered")
        else:
            logger.info("botji-observability: exporter disabled — hooks not registered")
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("botji-observability: init skipped (%s)", exc)
