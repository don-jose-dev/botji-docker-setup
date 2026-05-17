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


# PHRASES that indicate a true hard conflict — element added/removed/relocated.
# Phrase-based (not single-word) so that "extra open floor gap" (proportional drift)
# does not match "extra cabinet" (real addition). The previous single-word match on
# "extra" / "repositioned" / "structural" was firing on interior layout reviews
# because open floor space reads as "extra spacing" to the vision model — a
# sketch-to-render proportional drift, not an element addition.
_HARD_CONFLICT_PHRASES = (
    # Element additions (something that wasn't in the source)
    "added cabinet", "added module", "added appliance", "added tower", "added island",
    "added stool", "added plant", "added panel", "added shelf", "added door",
    "added element", "added object", "added handle", "added pendant", "added light fixture",
    "extra cabinet", "extra module", "extra appliance", "extra tower", "extra stool",
    "extra panel", "extra shelf", "extra plant", "extra door", "extra handle",
    "newly added", "new cabinet not", "new module not", "new tower not",
    "invented", "hallucinated", "fabricated",
    "not in the source image", "not in the source sketch", "not visible in source",
    "not present in source", "absent from source",
    # Element removals
    "missing cabinet", "missing module", "missing tower", "missing appliance",
    "missing stool", "missing panel", "missing element", "missing door",
    "removed cabinet", "removed module", "removed tower", "removed appliance",
    "removed element",
    # Count mismatches
    "count changed", "wrong count", "incorrect count",
    "wrong number of",
    # True reorders (module moved to different wall, not proportional shift)
    "swapped position", "swapped order", "swapped places",
    "wrong wall", "moved to wrong",
    "reordered modules", "reordered cabinets",
    # Adjacency violations (a real cabinet/panel inserted between two adjacent modules)
    "cabinet inserted between", "panel inserted between", "filler between",
    "cabinet between the oven", "cabinet between the ref", "cabinet between the tower",
)

# PHRASES that indicate proportional/perspective drift — soft, expected for sketch→render.
# These override the hard keywords above when both match (the soft context wins).
_SOFT_CONFLICT_PHRASES = (
    "extra open floor", "open floor space", "floor gap", "floor space",
    "open space between", "walking space",
    "proportional", "proportions",
    "appears repositioned", "appears expanded", "appears shifted", "appears compressed",
    "approximately", "approximate", "approximation",
    "slightly", "slight ", "minor ", "subtle",
    "interpretation", "interpreted as",
    "perspective", "depth perception", "depth perspective",
    "spacing differs", "spacing/layout", "exact spacing", "exact adjacency",
    "match exactly", "exactly match", "exactly preserved",
    "rendered view", "rendered surfaces", "rendered style",
    "style differs", "style interpretation",
    "lighting differs", "lighting interpretation",
    "finish interpretation",
)


# Verbs that indicate addition or removal of a discrete element.
_CHANGE_VERBS = (
    "added", "extra", "new ", "invented", "hallucinated", "fabricated",
    "missing", "removed", "absent", "deleted", "lost", "gone",
)

# Element nouns — when paired with a change verb in the same sentence,
# this is a real structural change (hard). Floor / spacing / adjacency are
# deliberately excluded — those describe relationships, not added objects.
_ELEMENT_NOUNS = (
    "cabinet", "module", "tower", "appliance", "stool", "chair",
    "panel", "shelf", "shelves", "island", "peninsula", "door",
    "drawer", "wardrobe", "pantry", "fridge", "refrigerator", "oven",
    "range", "hood", "sink", "faucet", "tap", "table", "bench",
    "plant", "vase", "bowl", "fruit", "lamp", "pendant", "fixture",
    "handle", "knob", "pull", "trim", "molding",
)

# Phrases that DEFINITELY mean "not in source" — element-add signal.
_SOURCE_NEGATION_PHRASES = (
    "not in source", "not in the source",
    "not visible in source", "not visible in the source",
    "not present in source", "absent from source",
    "wasn't in the source", "isn't in the source",
)


def _classify_conflict(text: str) -> str:
    """Return 'hard' or 'soft' based on phrases present in the conflict text.

    Decision order:
    1. SOFT phrases win first. If the conflict describes proportional drift
       (floor space, perspective, appearance, interpretation), it's soft —
       regardless of incidental module names mentioned for context.
    2. HARD phrases (specific add/remove/insert patterns) → hard.
    3. Verb+noun pattern (added/missing + a concrete element noun) → hard.
    4. Source-negation pattern ("not in the source") → hard.
    5. Default → soft. For sketch-to-render, ambiguous conflicts should warn,
       not block.
    """
    lower = text.lower()
    # 1. Soft phrases dominate. Even sentences that mention cabinets are soft
    #    when the conflict is framed as proportional drift.
    if any(phrase in lower for phrase in _SOFT_CONFLICT_PHRASES):
        return "soft"
    # 2. Explicit hard phrase match (e.g. "cabinet inserted between").
    if any(phrase in lower for phrase in _HARD_CONFLICT_PHRASES):
        return "hard"
    # 3. Generic verb+noun pattern: "added a wardrobe", "missing the tower".
    has_change_verb = any(v in lower for v in _CHANGE_VERBS)
    has_element_noun = any(n in lower for n in _ELEMENT_NOUNS)
    if has_change_verb and has_element_noun:
        return "hard"
    # 4. Source-negation phrasing.
    if any(p in lower for p in _SOURCE_NEGATION_PHRASES):
        return "hard"
    # 5. Default to soft (sketch-to-render leniency).
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

        # Verdict ladder, with sketch-to-render leniency built in:
        #
        # 1. If our phrase classifier found real hard conflicts (specific element
        #    added/removed/reordered) → BLOCK. The model and our filter agree.
        #
        # 2. If the model said "block" but our filter found NO hard conflicts
        #    AND matches dominate the response (at least as many matches as
        #    conflicts), trust our filter and DOWNGRADE TO WARN. This is the
        #    sketch-to-render leniency that prevents proportional drift from
        #    blocking valid renders. The vision model frequently calls "extra
        #    floor space" or "appears repositioned" a hard conflict; our
        #    phrase filter correctly classifies these as soft, and when the
        #    matches list shows the structure is preserved, we ship.
        #
        # 3. If the model said "block" with no soft signal either, respect it.
        #    This catches the case where the model is right and our keyword
        #    filter has a gap.
        #
        # 4. Otherwise warn or pass.
        match_count = len(matches)
        conflict_count = len(soft) + len(hard) + len(legacy if (not hard and not soft) else [])
        structure_preserved = bool(match_count) and match_count >= max(1, conflict_count)

        if hard:
            verdict = "block"
            blockers = hard
        elif raw_verdict in {"block", "fail", "failed", "conflict", "conflicted"} and structure_preserved:
            # Model over-blocked on proportional drift; our filter found no hard
            # signals and the matches show structural preservation. Downgrade.
            verdict = "warn"
            blockers = []
        elif raw_verdict in {"block", "fail", "failed", "conflict", "conflicted"}:
            # Model says block, no hard via filter, and matches don't dominate —
            # respect the model's call.
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


