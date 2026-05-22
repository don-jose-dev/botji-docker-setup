"""Deterministic + advisory-judge skill-interaction eval runner.

What this is
------------
A small runner for YAML fixtures that exercise the *interactions* between
Botji skills (parallel-render × source-current, render-router × 2d-to-3d,
retry-budget overrun, ...). Two-tier scoring is in place:

- Tier 1 (deterministic): structural assertions on the tool-call
  sequence. This is the only gate — exit code reflects only this tier.
- Tier 2 (judge, opt-in via ``--judge``): LLM-based semantic verdicts
  against each fixture's ``expected_semantic`` block. **Advisory only**
  in this PR — verdicts are PRINTED but never alter the exit code. See
  README for promotion-to-blocking criteria.

~80% of skill-interaction regressions can be caught by Tier 1 alone.
The judge fills the remaining semantic-vs-structural gap (e.g. a
structurally-valid review whose prose contradicts the source intent).
Each fixture carries a ``mock_trace`` that stands in for a real agent
run; when the runner is wired to live agent runs, that field is
replaced by the recorded trace but the check engine and fixtures stay
identical.

Inspiration: promptfoo (YAML fixtures + assertion model). Anthropic and
OpenAI both use promptfoo internally; we mirror its shape but keep the
runner small enough to maintain solo.

Layout
------
- run_evals.py        — this file: loader + check engine + CLI
- judge.py            — Tier 2 LLM-judge module (advisory only)
- judge_prompt.md     — judge system prompt
- fixtures/*.yaml     — one fixture per scenario
- README.md           — what each fixture exercises and how to add new ones

Run locally
-----------
    python evals/skill_interactions/run_evals.py
    python evals/skill_interactions/run_evals.py --judge   # add Tier 2
    make eval   # if you have GNU make

Exit codes: 0 all deterministic checks pass, 1 any deterministic fail,
2 fixture loading error. **Judge verdicts never affect the exit code.**
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures"

# Tool-name → must match exactly; case-sensitive. Aligns with hermes plugin schema.
NATIVE_ID_PREFIXES = ("src_", "out_", "rcpt_")
LEGACY_ID_PREFIXES = ("art_",)

NOISE_VOCABULARY = {
    "luxury", "elegant", "stunning", "breathtaking", "exquisite",
    "ultimate", "premium-tier", "world-class", "best-in-class",
    "cutting-edge", "state-of-the-art", "innovative",
    "seamless", "synergy", "leverage", "robust", "best-of-breed",
}


def load_fixtures(fixture_dir: Path) -> list[dict[str, Any]]:
    """Load every *.yaml in fixture_dir. Raises on parse error."""
    fixtures = []
    for path in sorted(fixture_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            fixture = yaml.safe_load(handle)
        if not isinstance(fixture, dict) or "id" not in fixture:
            raise ValueError(f"{path}: fixture missing required 'id' field")
        fixture["_path"] = str(path)
        fixtures.append(fixture)
    return fixtures


def _calls(trace: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    return [step for step in trace if step.get("tool") == tool]


def check_tool_call_present(trace: list[dict[str, Any]], params: dict[str, Any]) -> str | None:
    tool = params["tool"]
    min_count = int(params.get("min_count", 1))
    found = len(_calls(trace, tool))
    if found < min_count:
        return f"expected ≥{min_count} call(s) to {tool}, found {found}"
    return None


def check_tool_call_absent(trace: list[dict[str, Any]], params: dict[str, Any]) -> str | None:
    tool = params["tool"]
    found = len(_calls(trace, tool))
    if found > 0:
        return f"expected 0 calls to {tool}, found {found}"
    return None


def check_no_id_family_mix(trace: list[dict[str, Any]], _params: dict[str, Any]) -> str | None:
    """No call may pass both src_*/out_*/rcpt_* and art_* IDs in the same args."""
    for step in trace:
        args = step.get("args") or {}
        all_ids: list[str] = []
        for v in args.values():
            if isinstance(v, str):
                all_ids.append(v)
            elif isinstance(v, list):
                all_ids.extend(x for x in v if isinstance(x, str))
        native = any(any(s.startswith(p) for p in NATIVE_ID_PREFIXES) for s in all_ids)
        legacy = any(any(s.startswith(p) for p in LEGACY_ID_PREFIXES) for s in all_ids)
        if native and legacy:
            return f"{step.get('tool')} mixes native + legacy IDs in args: {all_ids}"
    return None


def check_lineage_match(trace: list[dict[str, Any]], _params: dict[str, Any]) -> str | None:
    """Every output's parents must be a subset of the registered source IDs."""
    sources = {
        step.get("result", {}).get("artifact_id")
        for step in trace
        if step.get("tool") in ("artifact_register", "source_register")
    }
    sources.discard(None)
    for step in trace:
        if step.get("tool") not in ("artifact_write", "artifact_transform"):
            continue
        parents = step.get("args", {}).get("parents") or step.get("result", {}).get("parents") or []
        unknown = [p for p in parents if p not in sources]
        if unknown:
            return f"{step.get('tool')} output has parents not in registered sources: {unknown}"
    return None


def check_transform_count_max(trace: list[dict[str, Any]], params: dict[str, Any]) -> str | None:
    max_per_branch = int(params["per_branch"])
    found = len(_calls(trace, "artifact_transform"))
    if found > max_per_branch:
        return f"artifact_transform called {found} times in one branch (cap {max_per_branch})"
    return None


def check_no_banned_vocabulary(trace: list[dict[str, Any]], _params: dict[str, Any]) -> str | None:
    fields = ("instructions", "mood_brief", "camera_brief", "light_brief")
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in NOISE_VOCABULARY) + r")\b", re.I)
    for step in trace:
        args = step.get("args") or {}
        for field in fields:
            text = args.get(field, "")
            if not isinstance(text, str):
                continue
            match = pattern.search(text)
            if match:
                return f"{step.get('tool')}.{field} contains banned vocabulary: '{match.group(0)}'"
    return None


CHECKS = {
    "tool_call_present": check_tool_call_present,
    "tool_call_absent": check_tool_call_absent,
    "no_id_family_mix": check_no_id_family_mix,
    "lineage_match": check_lineage_match,
    "transform_count_max": check_transform_count_max,
    "no_banned_vocabulary": check_no_banned_vocabulary,
}


def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Run all checks for a fixture and return a result dict."""
    trace = fixture.get("mock_trace") or fixture.get("trace") or []
    checks = fixture.get("deterministic_checks") or []
    failures: list[str] = []
    for spec in checks:
        kind = spec.get("kind")
        runner = CHECKS.get(kind)
        if runner is None:
            failures.append(f"unknown check kind: {kind}")
            continue
        msg = runner(trace, spec)
        if msg:
            failures.append(f"[{kind}] {msg}")
    return {
        "id": fixture["id"],
        "risk_pairs": fixture.get("risk_pairs") or [],
        "checks_run": len(checks),
        "failures": failures,
        "status": "pass" if not failures else "fail",
    }


def _fixture_cap() -> int:
    """Default 20; override via BOTJI_JUDGE_FIXTURE_CAP env var."""
    from judge import DEFAULT_FIXTURE_CAP  # local import keeps judge optional
    raw = os.environ.get("BOTJI_JUDGE_FIXTURE_CAP")
    if not raw:
        return DEFAULT_FIXTURE_CAP
    try:
        cap = int(raw)
        return cap if cap > 0 else DEFAULT_FIXTURE_CAP
    except ValueError:
        return DEFAULT_FIXTURE_CAP


def run_judge_tier(
    fixtures: list[dict[str, Any]],
    deterministic_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the LLM judge against fixtures that have ``expected_semantic``.

    Advisory only — the caller does NOT change exit code based on the
    returned verdicts. Returns (verdict list, summary dict).
    """
    from judge import JUDGE_COST_PER_FIXTURE_USD, JUDGE_MODEL, run_judge

    cap = _fixture_cap()
    eligible = [f for f in fixtures if f.get("expected_semantic")]
    selected = eligible[:cap]
    skipped_no_block = [f["id"] for f in fixtures if not f.get("expected_semantic")]
    skipped_cap = [f["id"] for f in eligible[cap:]]

    verdicts: list[dict[str, Any]] = []
    counts = {"pass": 0, "warn": 0, "block": 0, "inconclusive": 0}
    for fixture in selected:
        trace = fixture.get("mock_trace") or fixture.get("trace") or []
        verdict = run_judge(fixture, trace)
        counts[verdict.verdict] += 1
        verdicts.append({
            "id": fixture["id"],
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "reasoning": verdict.reasoning,
            "aligned_constraints": verdict.aligned_constraints,
            "violated_constraints": verdict.violated_constraints,
        })

    estimated_cost = round(len(selected) * JUDGE_COST_PER_FIXTURE_USD, 4)
    summary = {
        "model": JUDGE_MODEL,
        "fixture_cap": cap,
        "judged": len(selected),
        "skipped_no_expected_semantic": skipped_no_block,
        "skipped_cap": skipped_cap,
        "estimated_cost_usd": estimated_cost,
        "counts": counts,
        "advisory_only": True,
        "exit_code_affected": False,
        "deterministic_status": "pass" if all(
            r["status"] == "pass" for r in deterministic_results
        ) else "fail",
    }
    return verdicts, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic + advisory-judge skill-interaction evals.")
    parser.add_argument("--fixture-dir", default=str(FIXTURE_DIR))
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Also run the advisory LLM-judge tier. NEVER affects exit code.",
    )
    parser.add_argument(
        "--judge-report",
        default=None,
        help="Optional path to write judge verdicts as a JSON report.",
    )
    args = parser.parse_args()

    try:
        fixtures = load_fixtures(Path(args.fixture_dir))
    except Exception as exc:
        print(f"fixture load error: {exc}", file=sys.stderr)
        return 2

    results = [run_fixture(f) for f in fixtures]
    overall = "pass" if all(r["status"] == "pass" for r in results) else "fail"

    judge_verdicts: list[dict[str, Any]] = []
    judge_summary: dict[str, Any] | None = None
    if args.judge:
        # Sys.path shim so ``from judge import ...`` works regardless of
        # how the runner was invoked (module vs script).
        sys.path.insert(0, str(ROOT))
        try:
            judge_verdicts, judge_summary = run_judge_tier(fixtures, results)
        finally:
            try:
                sys.path.remove(str(ROOT))
            except ValueError:
                pass

    if args.json:
        payload: dict[str, Any] = {"status": overall, "results": results}
        if args.judge:
            payload["judge"] = {
                "summary": judge_summary,
                "verdicts": judge_verdicts,
            }
        print(json.dumps(payload, indent=2))
    else:
        for r in results:
            badge = "PASS" if r["status"] == "pass" else "FAIL"
            print(f"  {badge}  {r['id']}  ({r['checks_run']} checks)")
            for failure in r["failures"]:
                print(f"        - {failure}")
        print()
        print(f"overall: {overall}  ({sum(1 for r in results if r['status']=='pass')}/{len(results)} fixtures)")

        if args.judge and judge_summary is not None:
            print()
            print("--- judge tier (ADVISORY — does not affect exit code) ---")
            print(f"model: {judge_summary['model']}")
            print(f"judged: {judge_summary['judged']} fixture(s) "
                  f"(cap {judge_summary['fixture_cap']})")
            for v in judge_verdicts:
                tag = v["verdict"].upper()
                print(f"  [{tag}]  {v['id']}  (conf {v['confidence']:.2f})")
                print(f"        reasoning: {v['reasoning']}")
                for vc in v["violated_constraints"]:
                    print(f"        VIOLATED: {vc}")
            counts = judge_summary["counts"]
            print(f"\njudge counts: pass={counts['pass']} warn={counts['warn']} "
                  f"block={counts['block']} inconclusive={counts['inconclusive']}")
            print(f"estimated cost: ${judge_summary['estimated_cost_usd']:.4f} USD")
            if judge_summary["skipped_no_expected_semantic"]:
                print(f"skipped (no expected_semantic): "
                      f"{', '.join(judge_summary['skipped_no_expected_semantic'])}")
            if judge_summary["skipped_cap"]:
                print(f"skipped (cap): {', '.join(judge_summary['skipped_cap'])}")

    if args.judge_report and judge_summary is not None:
        report_path = Path(args.judge_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"summary": judge_summary, "verdicts": judge_verdicts}, indent=2),
            encoding="utf-8",
        )

    # Exit code reflects ONLY the deterministic tier. The judge is
    # advisory — see module docstring and README.
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
