"""Prometheus metrics for botji-core. Mechanical observation only.

Counts tool calls, times durations, tags verdict strings. No semantic
interpretation. See docs/OBSERVABILITY.md for the cardinality contract.
"""
from .exporter import TENANT, start_exporter
from .hooks import register_hooks

__all__ = ["TENANT", "register_hooks", "start_exporter"]
