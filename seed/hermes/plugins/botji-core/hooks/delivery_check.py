"""``transform_llm_output`` hook: last-line delivery enforcement.

Skills guide; hooks enforce. Backstop that rewrites the agent's final reply
when a source-bound output ships without a proper receipt. Complements the
post-review guard (``botji-artifacts/_guardrails.py``) and PR #34's
``pre_tool_call`` hook (catches tool args); this catches the user-visible text.

Three failure modes (first to fire wins; each REPLACES the reply):
  1. Reply mentions an ``out_*``/``art_*`` ID with delivery markers but no
     ``receipt_record`` exists in the receipts index for that output_id.
  2. Most recent receipt for that output_id has ``status: block``.
  3. Reply mentions an ``art_*`` whose ``current_turn_id`` doesn't match the
     most recent registered source's turn (stale lineage).

Trigger is conservative: BOTH a delivery marker (``route:``, ``claim:``,
``verdict:``, ``delivery_gate:``) AND a literal art_/out_ ID. Passthrough on:
casual chat (no markers/IDs), ``Allowed transform:`` design proposals, replies
that name a receipt_id, and replies the agent self-flagged with ``Internal:``.

Fail-open: any error logs and returns ``None`` (original ships).

Hook-context limitation: Hermes ``transform_llm_output`` only passes
``response_text``, ``session_id``, ``model``, ``platform`` — no tool-call
history. Detection uses artifact-ID mentions plus the substrate's JSONL index.
Replies that name no IDs cannot be checked (documented in the PR body).
"""
from __future__ import annotations
import logging, re
from typing import Any

logger = logging.getLogger(__name__)
_ID_RE = re.compile(r"\b(art|out)_\d{8}T\d{6}Z_[0-9a-f]{8}\b")
_RCPT_RE = re.compile(r"\brcpt_\d{8}T\d{6}Z_[0-9a-f]{8}\b")
_MARKERS = ("route:", "claim:", "verdict:", "delivery_gate:")
_PASS = ("allowed transform:", "receipt_id:", "❌ internal:")
_NO_RECEIPT = ("❌ Internal: source-bound output missing receipt. "
               "Not delivering. Retry with explicit receipt_record before final reply.")


def _delivery_like(text: str) -> bool:
    return any(m in text.lower() for m in _MARKERS)


def _passthrough(text: str) -> bool:
    return any(p in text.lower() for p in _PASS) or bool(_RCPT_RE.search(text))


def _latest_receipt_for(oid: str, receipts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((r for r in reversed(receipts) if str(r.get("output_id") or "") == oid), None)


def _latest_source_turn(sources: list[dict[str, Any]]) -> str:
    return next((str(r.get("current_turn_id") or "").strip() for r in reversed(sources)
                 if str(r.get("current_turn_id") or "").strip()), "")


def transform_llm_output(response_text: str = "", session_id: str = "",
                         **_: Any) -> str | None:
    """First failure mode to fire wins. Errors fail-open (log + None)."""
    try:
        if not response_text or not _delivery_like(response_text):
            return None
        if _passthrough(response_text):
            return None
        ids = {m.group(0) for m in _ID_RE.finditer(response_text)}
        if not ids:
            return None
        from .. import _read_records, _find_legacy_artifact  # type: ignore[attr-defined]
        receipts = _read_records("receipts")
        for art_id in sorted(ids):
            latest = _latest_receipt_for(art_id, receipts)
            if latest is None:
                logger.info("botji-core delivery_check: no receipt for %s", art_id)
                return _NO_RECEIPT
            if str(latest.get("status") or "").lower() == "block":
                blocker = str(latest.get("primary_blocker") or "unspecified")
                return (f"❌ Internal: receipt blocked for this output ({blocker}). "
                        "Not delivering.")
        current_turn = _latest_source_turn(_read_records("sources"))
        if current_turn:
            for art_id in sorted(i for i in ids if i.startswith("art_")):
                rec = _find_legacy_artifact(art_id)
                rt = str((rec or {}).get("current_turn_id") or "").strip()
                if rt and rt != current_turn:
                    return (f"❌ Internal: reply references stale lineage {art_id} "
                            "from an earlier turn. Not delivering.")
        return None
    except Exception as exc:  # noqa: BLE001 — fail-open is the policy.
        logger.warning("botji-core delivery_check hook failed open: %s", exc)
        return None
