"""Review engine — V1R declarative axes loader and dispatcher.

Loads ``axes.yaml`` and evaluates the applicable axes for a given source +
output evidence pair under a given route. Replaces the procedural branches
in ``botji-artifacts/_review.py``.

Each axis has an ``action``:
- ``delegate_to_adapter``: the engine calls the adapter's ``compare()`` and
  maps that result back to the axis's pass/fail verdict.
- ``substrate_check``: the engine handles the check itself (lineage, provider
  route presence). No adapter call.

Aggregation: block > warn > pass. If any axis blocks, the overall verdict is
block. Reasons are collected from every triggering axis. Returns a structured
review record suitable for receipt_record.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

_VERDICT_RANK = {"pass": 0, "warn": 1, "block": 2}
_RANK_VERDICT = {v: k for k, v in _VERDICT_RANK.items()}


@dataclass
class AxisResult:
    name: str
    verdict: str  # "pass" | "warn" | "block"
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewResult:
    """Aggregate of all axis results for a single source+output pair."""
    verdict: str  # "pass" | "warn" | "block"
    axis_results: list[AxisResult] = field(default_factory=list)
    primary_blocker: str | None = None  # first axis that emitted "block"


def _axes_path() -> Path:
    return Path(__file__).parent / "axes.yaml"


def _load_axes() -> dict[str, dict[str, Any]]:
    """Read axes.yaml into a {name: definition} dict."""
    path = _axes_path()
    if not path.is_file():
        logger.warning("botji-review: axes.yaml missing at %s", path)
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("botji-review: failed to load axes.yaml: %s", exc)
        return {}
    return data.get("axes") or {}


def _axis_applies(definition: dict[str, Any], context: dict[str, Any]) -> bool:
    """True if every applies_when key matches the context."""
    cond = definition.get("applies_when") or {}
    if not cond:
        return True
    for key, want in cond.items():
        if key.endswith("_not"):
            base = key[:-4]
            if context.get(base) == want:
                return False
        elif context.get(key) != want:
            return False
    return True


def _evaluate_substrate(
    axis_name: str,
    definition: dict[str, Any],
    context: dict[str, Any],
) -> AxisResult:
    """Substrate-level axis check. Currently lineage + provider-route only."""
    on_pass = definition.get("on_pass") or {"verdict": "pass", "reason": "ok"}
    on_fail = definition.get("on_fail") or {"verdict": "block", "reason": axis_name}

    if axis_name == "artifact_lineage":
        # Parents present and non-empty.
        parents = (context.get("output") or {}).get("parents") or []
        passed = bool(parents)
    elif axis_name == "provider_transform_proof":
        # Output evidence carries provider_route metadata.
        provider_route = (context.get("output") or {}).get("provider_route")
        passed = bool(provider_route)
    else:
        # Unknown substrate axis — fail safe.
        passed = False

    spec = on_pass if passed else on_fail
    return AxisResult(
        name=axis_name,
        verdict=str(spec.get("verdict", "warn")),
        reason=str(spec.get("reason", axis_name)),
    )


def _evaluate_adapter(
    axis_name: str,
    definition: dict[str, Any],
    context: dict[str, Any],
    adapter: Any,
    source_evidence: Any,
    output_evidence: Any,
    policy: Any,
) -> AxisResult:
    """Call adapter.compare() and map its result back to the axis verdict."""
    on_pass = definition.get("on_pass") or {"verdict": "pass", "reason": "ok"}
    on_fail = definition.get("on_fail") or {"verdict": "warn", "reason": axis_name}

    try:
        cmp_result = adapter.compare(source_evidence, output_evidence, policy)
    except Exception as exc:  # noqa: BLE001 — engine never crashes
        logger.warning("botji-review: adapter.compare raised for %s: %s", axis_name, exc)
        return AxisResult(
            name=axis_name,
            verdict="warn",
            reason=f"{axis_name}_evaluator_error",
            fields={"error": str(exc)[:200]},
        )

    if cmp_result.verdict == "pass":
        return AxisResult(
            name=axis_name,
            verdict=str(on_pass.get("verdict", "pass")),
            reason=str(on_pass.get("reason", axis_name)),
            fields=cmp_result.fields,
        )
    # warn or block from compare → map to axis's on_fail verdict, but
    # never SOFTEN the comparator's signal: if adapter said "block" we
    # block regardless of axis on_fail.
    fail_verdict = str(on_fail.get("verdict", "warn"))
    if _VERDICT_RANK.get(cmp_result.verdict, 0) > _VERDICT_RANK.get(fail_verdict, 0):
        fail_verdict = cmp_result.verdict
    return AxisResult(
        name=axis_name,
        verdict=fail_verdict,
        reason=str(on_fail.get("reason", axis_name)),
        fields=cmp_result.fields,
    )


def review(
    source_evidence: Any,
    output_evidence: Any,
    policy: Any,
    context: dict[str, Any] | None = None,
    adapter: Any = None,
) -> ReviewResult:
    """Run every applicable axis. Returns aggregated ReviewResult.

    ``context`` carries fields used by ``applies_when`` matching: ``route``,
    ``adapter`` (name), ``strict``, plus per-axis evidence shortcuts the
    substrate checks consult (``output.parents``, ``output.provider_route``).
    """
    axes = _load_axes()
    ctx = dict(context or {})
    if "adapter" not in ctx and adapter is not None:
        ctx["adapter"] = getattr(adapter, "name", None)
    if "route" not in ctx and policy is not None:
        ctx["route"] = getattr(policy, "route", None)

    axis_results: list[AxisResult] = []
    for axis_name, definition in axes.items():
        if not _axis_applies(definition, ctx):
            continue
        action = definition.get("action", "delegate_to_adapter")
        if action == "substrate_check":
            axis_results.append(_evaluate_substrate(axis_name, definition, ctx))
        elif action == "delegate_to_adapter":
            if adapter is None:
                # No adapter wired — record as warn instead of crashing.
                axis_results.append(AxisResult(
                    name=axis_name, verdict="warn",
                    reason=f"{axis_name}_no_adapter",
                ))
                continue
            axis_results.append(_evaluate_adapter(
                axis_name, definition, ctx,
                adapter, source_evidence, output_evidence, policy,
            ))
        else:
            logger.warning("botji-review: unknown axis action %r for %s", action, axis_name)
            continue

    # Aggregate
    if not axis_results:
        return ReviewResult(verdict="pass", axis_results=[], primary_blocker=None)
    max_rank = max(_VERDICT_RANK.get(a.verdict, 0) for a in axis_results)
    verdict = _RANK_VERDICT[max_rank]
    primary_blocker: str | None = None
    if verdict == "block":
        for axis in axis_results:
            if axis.verdict == "block":
                primary_blocker = axis.name
                break
    return ReviewResult(
        verdict=verdict,
        axis_results=axis_results,
        primary_blocker=primary_blocker,
    )


def list_axes() -> list[str]:
    """List the names of every axis defined in axes.yaml."""
    return list(_load_axes().keys())
