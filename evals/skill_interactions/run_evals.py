"""Deterministic skill-interaction eval runner — bootstrap.

What this is
------------
A small runner for YAML fixtures that exercise the *interactions* between
Botji skills (parallel-render × source-current, render-router × 2d-to-3d,
retry-budget overrun, ...). Two-tier scoring is planned; this bootstrap
ships the deterministic tier only.

Why deterministic-only for now
------------------------------
~80% of skill-interaction regressions can be caught by structural assertions
on the recorded tool-call sequence and lineage graph — no LLM judge needed.
The judge tier is the follow-up PR. Each fixture carries a ``mock_trace``
that stands in for a real agent run; when the runner is wired to live agent
runs, that field is replaced by the recorded trace but the check engine and
fixtures stay identical.

Inspiration: promptfoo (YAML fixtures + assertion model). Anthropic and
OpenAI both use promptfoo internally; we mirror its shape but keep the
runner small enough to maintain solo.

Layout
------
- run_evals.py        — this file: loader + check engine + CLI (no classes;
                       no function over ~30 lines; not a god file)
- fixtures/*.yaml     — one fixture per scenario
- README.md           — what each fixture exercises and how to add new ones

Run locally
-----------
    python evals/skill_interactions/run_evals.py
    make eval   # if you have GNU make

Exit codes: 0 all pass, 1 any fail, 2 fixture loading error.
"""
from __future__ import annotations

import argparse
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic skill-interaction evals.")
    parser.add_argument("--fixture-dir", default=str(FIXTURE_DIR))
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    try:
        fixtures = load_fixtures(Path(args.fixture_dir))
    except Exception as exc:
        print(f"fixture load error: {exc}", file=sys.stderr)
        return 2

    results = [run_fixture(f) for f in fixtures]
    overall = "pass" if all(r["status"] == "pass" for r in results) else "fail"

    if args.json:
        print(json.dumps({"status": overall, "results": results}, indent=2))
    else:
        for r in results:
            badge = "PASS" if r["status"] == "pass" else "FAIL"
            print(f"  {badge}  {r['id']}  ({r['checks_run']} checks)")
            for failure in r["failures"]:
                print(f"        - {failure}")
        print()
        print(f"overall: {overall}  ({sum(1 for r in results if r['status']=='pass')}/{len(results)} fixtures)")

    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
