"""Botji V1 delivery-PDF plugin.

Generates a one-page A4 PDF receipt per delivery, attached alongside the
rendered image. The substrate (``botji-core``) calls ``generate_receipt_pdf``
from ``_handle_delivery_gate`` once the gate clears or warns. Failures here
must NOT block delivery — the substrate wraps the call in try/except.

Accessed by direct import:
    from _pdf import generate_receipt_pdf
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:  # noqa: ARG001 — no tools, no hooks
    # Lazy probe — the actual PDF call is invoked from botji-core; an env
    # without fpdf2 still loads cleanly so the substrate keeps serving.
    try:
        from _pdf import generate_receipt_pdf  # noqa: F401
        logger.info("botji-receipt: registered (PDF generator ready)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("botji-receipt: PDF generator unavailable (%s); delivery PDFs disabled", exc)
