"""Mechanical fidelity guardrails that do not depend on agent compliance."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from _registry import _load_artifact, _load_records
from _utils import _hermes_home, _sha256


_IMAGE_ATTACHMENT_RE = re.compile(r"\[Image attached at:\s*([^\]\n]+)\]")
_OPT_DATA_PATH_RE = re.compile(r"(/opt/data/(?:image_cache|artifacts|uploads|files)/[^\s\]\"')]+)")


def _iter_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
            elif isinstance(item, str):
                texts.append(item)
        return texts
    if isinstance(value, dict):
        text = value.get("text")
        return [text] if isinstance(text, str) else []
    return []


def _attached_paths_from_content(content: Any) -> list[str]:
    paths: list[str] = []
    for text in _iter_text(content):
        for match in _IMAGE_ATTACHMENT_RE.finditer(text):
            paths.append(match.group(1).strip())
        for match in _OPT_DATA_PATH_RE.finditer(text):
            paths.append(match.group(1).strip())
    return list(dict.fromkeys(paths))


def _session_jsonl_path(session_id: str) -> Path | None:
    sessions = _hermes_home() / "sessions"
    direct = sessions / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    matches = sorted(sessions.glob(f"*{session_id}*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def latest_user_attachment_paths(session_id: str) -> list[str]:
    path = _session_jsonl_path(session_id)
    if path is None:
        return []
    latest: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if record.get("role") != "user":
            continue
        found = _attached_paths_from_content(record.get("content"))
        if found:
            latest = found
    return latest


def _maybe_sha(path: str) -> str | None:
    try:
        candidate = Path(path)
        if candidate.is_file():
            return _sha256(candidate)
    except Exception:
        return None
    return None


def _record_for_attachment(attachment_path: str, attachment_sha: str | None) -> dict[str, Any] | None:
    """Find the most recent legacy registry record for an attachment path or sha.

    Walks the index newest-first so a re-uploaded file resolves to its most
    recent registration. Returns None when the attachment is not yet registered.
    """
    for record in reversed(_load_records()):
        if record.get("original_path") == attachment_path or record.get("path") == attachment_path:
            return record
        if attachment_sha and record.get("sha256") == attachment_sha:
            return record
    return None


def _turns_disagree(source: dict[str, Any], attachment_record: dict[str, Any] | None) -> bool:
    """Detect when source and attachment are tagged with different turns.

    Both sides must carry a non-empty current_turn_id for disagreement to be
    actionable; a missing turn on either side is treated as "indeterminate" and
    falls back to the historical SHA/path behaviour to keep pre-fix records usable.
    """
    if attachment_record is None:
        return False
    src_turn = str(source.get("current_turn_id") or "").strip()
    att_turn = str(attachment_record.get("current_turn_id") or "").strip()
    if not src_turn or not att_turn:
        return False
    return src_turn != att_turn


def _source_matches_attachment(source: dict[str, Any], attachment_path: str, attachment_sha: str | None) -> bool:
    source_paths = [str(source.get("original_path") or ""), str(source.get("path") or "")]
    if attachment_path in source_paths:
        return True

    # SHA fallback handles re-uploads of identical bytes under new image-cache
    # paths. Reject the fallback when source and attachment are tagged with
    # different turns — same bytes from a stale turn must not silently reuse
    # the prior turn's lineage record (see commit 1bfcd89 backstop).
    attachment_record = _record_for_attachment(attachment_path, attachment_sha)
    if _turns_disagree(source, attachment_record):
        return False

    source_sha = str(source.get("sha256") or "")
    if attachment_sha and source_sha == attachment_sha:
        return True
    for source_path in source_paths:
        if source_path and (source_sha := _maybe_sha(source_path)) and attachment_sha and source_sha == attachment_sha:
            return True
    return False


def _block_review(review: dict[str, Any], blocker: str, correction: str, *, details: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(review)
    updated["verdict"] = "block"
    updated["delivery_gate"] = "blocked"
    updated["recommended_action"] = "do_not_deliver__retry_with_current_source"
    updated["primary_blocker"] = blocker
    updated["retry_guidance"] = correction
    updated.setdefault("blockers", [])
    if blocker not in updated["blockers"]:
        updated["blockers"].insert(0, blocker)
    updated.setdefault("required_corrections", [])
    if correction not in updated["required_corrections"]:
        updated["required_corrections"].insert(0, correction)
    updated.setdefault("axes", []).append({
        "axis": "current_turn_source",
        "compare_status": "conflict",
        "severity": "blocking",
        "notes": blocker,
        "claim_level": "verified",
        "evidence_ids": [],
    })
    context = updated.setdefault("artifact_context", {})
    context["current_turn_source_guard"] = {"status": "blocked", **details}
    scores = updated.setdefault("fidelity_scores", {})
    scores["transform_contract_fidelity_percent"] = 0
    scores["preservation_target"] = "current_turn_source_lineage"
    return updated


def apply_current_turn_source_guard(review: dict[str, Any], session_id: str) -> tuple[dict[str, Any], bool]:
    """Block source-bound reviews that do not use the latest attached source.

    Exact path match is accepted. If the user re-sent the same bytes under a new
    image-cache path, SHA-256 match is also accepted to avoid blocking duplicate
    uploads.
    """
    latest_paths = latest_user_attachment_paths(session_id)
    if not latest_paths:
        return review, False

    source_ids = [
        str(item) for item in (review.get("artifact_context") or {}).get("source_artifact_ids") or []
        if str(item).strip()
    ]
    if not source_ids:
        return _block_review(
            review,
            "Current user turn has an attachment, but the review has no source artifact lineage.",
            "Register the current attachment and regenerate/review with that source_artifact_id.",
            details={"latest_attachment_paths": latest_paths, "source_artifact_ids": source_ids},
        ), True

    sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        try:
            sources.append(_load_artifact(source_id))
        except Exception:
            pass

    missing: list[str] = []
    checks: list[dict[str, Any]] = []
    for attachment_path in latest_paths:
        attachment_sha = _maybe_sha(attachment_path)
        matched = any(_source_matches_attachment(source, attachment_path, attachment_sha) for source in sources)
        checks.append({"path": attachment_path, "sha256": attachment_sha, "matched": matched})
        if not matched:
            missing.append(attachment_path)

    if not missing:
        if review.get("artifact_context") is not None:
            review.setdefault("artifact_context", {})["current_turn_source_guard"] = {
                "status": "passed",
                "latest_attachment_paths": latest_paths,
                "source_artifact_ids": source_ids,
                "checks": checks,
            }
        return review, False

    source_paths = [
        {
            "artifact_id": source.get("artifact_id"),
            "path": source.get("path"),
            "original_path": source.get("original_path"),
            "sha256": source.get("sha256"),
        }
        for source in sources
    ]
    blocker = (
        "Current-turn source mismatch: the reviewed output does not use the latest "
        f"user attachment(s): {', '.join(missing)}"
    )
    correction = "Register the latest attachment from this user turn and regenerate with source_artifact_ids set to that artifact."
    return _block_review(
        review,
        blocker,
        correction,
        details={
            "latest_attachment_paths": latest_paths,
            "missing_attachment_paths": missing,
            "source_artifact_ids": source_ids,
            "source_paths": source_paths,
            "checks": checks,
        },
    ), True


__all__ = ["latest_user_attachment_paths", "apply_current_turn_source_guard"]
