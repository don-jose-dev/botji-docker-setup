"""Vision review prompts, JSON parsing, conflict classification, and payload assessment."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from _prompts import load_prompt


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _vision_review_prompt(fidelity_requirements: list[str]) -> str:
    if fidelity_requirements:
        requirements = "\n".join(f"- {item}" for item in fidelity_requirements)
    else:
        requirements = "- Preserve visible source layout, object identity, proportions, text/labels, and avoid invented elements."
    return load_prompt("vision_review").replace("{{REQUIREMENTS}}", requirements)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _coerce_review_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
    text = str(value).strip()
    return [text] if text else []


_HARD_CONFLICT_KEYWORDS = frozenset({
    "added", "extra", "invented", "hallucinated", "not in source", "not visible in source",
    "missing", "removed", "absent", "count changed", "count wrong", "wrong count",
    "reorder", "reordered", "wrong position", "repositioned", "structural",
    "not present", "does not exist", "extra element",
})


def _classify_conflict(text: str) -> str:
    """Return 'hard' or 'soft' based on conflict text keywords."""
    lower = text.lower()
    if any(kw in lower for kw in _HARD_CONFLICT_KEYWORDS):
        return "hard"
    return "soft"


def _assess_vision_payload(vision_payload: dict[str, Any]) -> dict[str, Any]:
    text = str(vision_payload.get("comparison") or "").strip()
    data = _extract_json_object(text)
    if data is not None:
        raw_verdict = str(data.get("verdict") or "").strip().lower()

        # New structured fields (hard/soft split)
        hard = _coerce_review_items(data.get("hard_conflicts") or [])
        soft = _coerce_review_items(data.get("soft_conflicts") or [])

        # Legacy field — classify by keyword if hard/soft not provided
        legacy = _coerce_review_items(data.get("conflicts") or data.get("blocking_conflicts") or [])
        if legacy and not hard and not soft:
            for c in legacy:
                if _classify_conflict(c) == "hard":
                    hard.append(c)
                else:
                    soft.append(c)

        partials = _coerce_review_items(data.get("partials") or [])
        unknowns = _coerce_review_items(data.get("unknowns") or [])
        matches = _coerce_review_items(data.get("matches") or [])
        corrections = _coerce_review_items(data.get("required_corrections") or data.get("corrections") or [])

        # Hard conflicts always block; soft conflicts only warn
        if hard:
            verdict = "block"
            blockers = hard
        elif raw_verdict in {"block", "fail", "failed", "conflict", "conflicted"}:
            # Model said block but no hard_conflicts extracted — treat as hard block
            verdict = "block"
            blockers = legacy or [f"Vision review verdict is {raw_verdict}."]
        elif soft or partials or raw_verdict in {"warn", "warning", "partial", "mixed", "uncertain"}:
            verdict = "warn"
            blockers = []
        else:
            verdict = "pass"
            blockers = []

        if verdict == "block" and not corrections:
            corrections = ["Regenerate until all hard fidelity conflicts are resolved."]

        summary = json.dumps(
            {
                "verdict": verdict,
                "matches": matches[:8],
                "partials": (soft + partials)[:8],
                "conflicts": blockers[:8],
                "unknowns": unknowns[:8],
            },
            ensure_ascii=False,
        )
        return {
            "verdict": verdict,
            "blockers": blockers,
            "corrections": corrections,
            "summary": summary[:1600],
        }

    lowered = text.lower()
    block_signals = (
        '"verdict": "block"', '"verdict":"block"',
        '"verdict": "fail"', '"verdict":"fail"',
        "blocking conflict", "not faithful", "does not preserve",
        "wrong layout", "violates",
    )
    warn_signals = ('"verdict": "warn"', '"verdict":"warn"', "minor drift", "partial")
    if any(signal in lowered for signal in block_signals):
        return {
            "verdict": "block",
            "blockers": [f"Vision review reported a fidelity conflict: {text[:400]}"],
            "corrections": ["Regenerate until all hard fidelity conflicts are resolved."],
            "summary": text[:1600],
        }
    if any(signal in lowered for signal in warn_signals):
        return {"verdict": "warn", "blockers": [], "corrections": [], "summary": text[:1600]}
    return {"verdict": "pass", "blockers": [], "corrections": [], "summary": text[:1600] or "Vision review returned no text."}


