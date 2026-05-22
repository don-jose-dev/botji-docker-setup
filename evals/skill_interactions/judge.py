"""LLM-judge advisory tier for skill-interaction evals.

Purpose
-------
The deterministic tier (run_evals.py) catches structural regressions in
tool-call traces. It cannot catch SEMANTIC regressions — e.g. an agent
that produces a structurally-valid review whose prose contradicts the
source intent. This module fills that gap with a small structured-output
LLM call against each fixture's ``expected_semantic`` block.

Advisory only
-------------
The runner PRINTS verdicts but does not change exit code based on them.
Promoting judge to blocking is a future PR, gated on judge stability
≥95% on baseline. See README for promotion criteria.

Failure mode
------------
Any error reaching the OpenAI API (auth, rate limit, network) returns
``inconclusive`` with the reason in ``reasoning``. The runner never
crashes because of the judge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# Model used for the judge. Mirrors the chat model configured in
# seed/hermes/config.yaml (model.default). gpt-5.4-mini is the cheapest
# OpenAI chat model that supports strict structured output today.
JUDGE_MODEL = "gpt-5.4-mini"

# Per-fixture cost estimate (USD). gpt-5.4-mini Responses API with a
# small structured output schema is ~$0.01–0.05 per call depending on
# trace length. We use the high end here so the printed estimate is
# slightly conservative rather than slightly optimistic.
JUDGE_COST_PER_FIXTURE_USD = 0.05

# Default cap on fixtures sent to the judge per --judge run. Cost
# guardrail: 20 × $0.05 = $1.00 worst case. Override via
# BOTJI_JUDGE_FIXTURE_CAP env var when explicitly running broader.
DEFAULT_FIXTURE_CAP = 20

PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.md"


class JudgeVerdict(BaseModel):
    """Structured output schema returned by the judge."""

    model_config = {"extra": "forbid"}

    verdict: Literal["pass", "warn", "block", "inconclusive"] = Field(
        description="Overall semantic verdict. Default inconclusive on uncertainty."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Judge confidence in the verdict, 0.0–1.0.",
    )
    reasoning: str = Field(
        max_length=500,
        description="Terse explanation (≤500 chars).",
    )
    aligned_constraints: list[str] = Field(
        default_factory=list,
        description="expected_semantic statements clearly satisfied by the trace.",
    )
    violated_constraints: list[str] = Field(
        default_factory=list,
        description="expected_semantic statements clearly contradicted by the trace.",
    )


def _inconclusive(reason: str) -> JudgeVerdict:
    """Build the safe-default verdict when the judge cannot run."""
    return JudgeVerdict(
        verdict="inconclusive",
        confidence=0.0,
        reasoning=reason[:500],
        aligned_constraints=[],
        violated_constraints=[],
    )


def _load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(judge prompt not found at {PROMPT_PATH}: {exc})"


def _build_user_payload(fixture: dict[str, Any], trace: list[dict[str, Any]]) -> str:
    """Compact JSON the judge reads as the user message."""
    payload = {
        "fixture": {
            "id": fixture.get("id"),
            "description": fixture.get("description"),
            "risk_pairs": fixture.get("risk_pairs") or [],
            "expected_semantic": fixture.get("expected_semantic") or [],
        },
        "trace": trace,
    }
    return json.dumps(payload, indent=2, default=str)


def run_judge(fixture: dict[str, Any], trace: list[dict[str, Any]]) -> JudgeVerdict:
    """Run the judge against one fixture+trace pair.

    Returns ``inconclusive`` (never raises) on any failure path. The
    caller is the runner; it prints the verdict but does not change
    overall pass/fail based on it.
    """
    expected = fixture.get("expected_semantic") or []
    if not expected:
        return _inconclusive("fixture has no expected_semantic block; skipping judge")

    if not os.environ.get("OPENAI_API_KEY"):
        return _inconclusive("OPENAI_API_KEY is not set; judge cannot authenticate")

    # Import lazily so the deterministic tier never hard-depends on the
    # openai package being importable. Any import error → inconclusive.
    try:
        from openai import OpenAI
    except Exception as exc:
        return _inconclusive(f"openai package import failed: {exc!r}")

    try:
        client = OpenAI()
    except Exception as exc:
        return _inconclusive(f"OpenAI client init failed: {exc!r}")

    instructions = _load_prompt()
    user_payload = _build_user_payload(fixture, trace)

    # JSON-schema for Responses API structured output. Derived from the
    # Pydantic model so any drift between the two is detectable here.
    # OpenAI strict mode requires every property be in ``required`` and
    # ``additionalProperties`` be false.
    schema = JudgeVerdict.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}).keys())

    try:
        response = client.responses.create(
            model=JUDGE_MODEL,
            store=False,
            instructions=instructions,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": user_payload},
            ]}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "JudgeVerdict",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
    except Exception as exc:
        # Auth, rate limit, network, model-not-available — all collapse
        # to inconclusive. The exception class is captured for debug.
        return _inconclusive(f"OpenAI Responses call failed: {type(exc).__name__}: {exc}")

    text = getattr(response, "output_text", None) or ""
    if not text:
        return _inconclusive("OpenAI response did not include output_text")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _inconclusive(f"judge output was not valid JSON: {exc}")

    try:
        return JudgeVerdict.model_validate(parsed)
    except Exception as exc:
        return _inconclusive(f"judge output failed schema validation: {exc}")
