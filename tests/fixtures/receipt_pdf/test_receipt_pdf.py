"""Real (no-mock) end-to-end test for the Botji V1 PDF receipt generator.

Run from the repo root:

    python tests/fixtures/receipt_pdf/test_receipt_pdf.py

This intentionally does NOT plug into the YAML-based ``botji-harness`` runner:
the harness is built around tool-call sequences with key/value assertions, not
binary-file inspection. The verification we need here (file exists, > 5 KB,
parseable PDF with one page and metadata) is straight Python and lives next
to the fixture data.

Asserts:
    1. ``generate_receipt_pdf`` produces a valid one-page PDF with metadata.
    2. The wired ``_handle_delivery_gate`` returns ``receipt_pdf_path``
       referencing that PDF when called with a realistic clear receipt.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[3]
RECEIPT_PLUGIN = REPO_ROOT / "seed" / "hermes" / "plugins" / "botji-receipt"
CORE_PLUGIN = REPO_ROOT / "seed" / "hermes" / "plugins" / "botji-core" / "__init__.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _make_png(path: Path, colour: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), colour).save(path, "PNG")
    return path


def _make_palette_png(path: Path, base_colour: tuple[int, int, int]) -> Path:
    """Generate a palette-mode PNG to exercise the Pillow-normalisation footgun."""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGB", (512, 512), base_colour)
    base.convert("P", palette=Image.Palette.ADAPTIVE).save(path, "PNG")
    return path


def _receipt_fixture(receipt_id: str, source_id: str, output_id: str, current_turn: str) -> dict:
    return {
        "receipt_id": receipt_id,
        "source_ids": [source_id],
        "output_id": output_id,
        "route": "image_edit",
        "status": "pass",
        "claim_level": "reviewed",
        "current_turn_id": current_turn,
        "primary_blocker": None,
        "user_visible_summary": "Render passed all fidelity gates.",
        "created_at": "2026-05-25T12:00:00Z",
        "checks": [{"name": "lineage", "status": "pass"}],
        # Optional receipt extensions consumed by the PDF layout:
        "preserved": ["overall geometry", "panel topology", "label placement", "door swings", "north arrow"],
        "changed": ["render style → cad-elevation", "annotation density reduced"],
        "generated": ["fidelity report", "elevation legend", "C2PA manifest", "render receipt"],
        "fidelity_scores": {
            "coverage": "match",
            "ground": "match",
            "safety": "match",
            "lineage": "match",
            "preserve_change": "match",
            "actionability": "match",
        },
    }


def _assert_valid_pdf(pdf_path: Path) -> dict:
    assert pdf_path.is_file(), f"PDF not written: {pdf_path}"
    size = pdf_path.stat().st_size
    assert size > 5 * 1024, f"PDF too small ({size} bytes), expected > 5 KB"
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    assert page_count == 1, f"expected 1 page, got {page_count}"
    meta = reader.metadata or {}
    title = str(meta.get("/Title") or "")
    author = str(meta.get("/Author") or "")
    assert "Botji" in title or "Botji" in author, f"metadata missing Botji branding: title={title!r} author={author!r}"
    return {"size": size, "pages": page_count, "title": title, "author": author}


def case_generator_direct(tmp: Path) -> dict:
    """1. Direct call to ``generate_receipt_pdf`` with real PNGs."""
    sys.path.insert(0, str(RECEIPT_PLUGIN))
    try:
        from _pdf import generate_receipt_pdf  # type: ignore
    finally:
        sys.path.pop(0)
    src = _make_palette_png(tmp / "incoming" / "source.png", (220, 30, 30))
    out = _make_png(tmp / "incoming" / "output.png", (40, 90, 220))
    receipt = _receipt_fixture(
        receipt_id="rcpt_TEST_direct",
        source_id="src_TEST_direct",
        output_id="out_TEST_direct",
        current_turn="turn-direct",
    )
    pdf_path = generate_receipt_pdf(
        receipt=receipt,
        source_image_path=str(src),
        output_image_path=str(out),
        output_dir=tmp / "receipts" / "rcpt_TEST_direct",
        c2pa_ref="urn:c2pa:test:abcdef0123456789",
    )
    info = _assert_valid_pdf(pdf_path)
    info["path"] = str(pdf_path)
    return info


def case_wired_delivery_gate(tmp: Path) -> dict:
    """2. End-to-end: drive source_register -> artifact_write -> receipt_record -> delivery_gate."""
    home = tmp / "hermes-home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["BOTJI_CORE_ROOT"] = str(home / "botji-core")
    os.environ["BOTJI_ARTIFACT_ROOT"] = str(home / "artifacts")
    os.environ["BOTJI_RECEIPT_PDF_ENABLED"] = "1"

    sys.path.insert(0, str(RECEIPT_PLUGIN))
    try:
        core = _load_module(f"botji_core_test_{uuid.uuid4().hex[:8]}", CORE_PLUGIN)
    finally:
        sys.path.pop(0)

    class _Ctx:
        def __init__(self):
            self.tools = {}
            self.hooks = {}

        def register_tool(self, *, name, handler, **_):
            self.tools[name] = handler

        def register_hook(self, hook_name, callback):
            self.hooks.setdefault(hook_name, []).append(callback)

    ctx = _Ctx()
    core.register(ctx)

    def call(name: str, args: dict) -> dict:
        return json.loads(ctx.tools[name](args))

    # Real input files under HERMES_HOME so _resolve_allowed_path passes.
    src_path = _make_png(home / "incoming" / "source.png", (220, 30, 30))
    out_path = _make_png(home / "incoming" / "output.png", (40, 90, 220))

    src = call("source_register", {
        "path": str(src_path),
        "current_turn_id": "turn-e2e",
        "role": "source",
        "declared_type": "image",
    })
    assert src["success"], f"source_register failed: {src}"

    out = call("artifact_write", {
        "path": str(out_path),
        "parents": [src["artifact_id"]],
        "current_turn_id": "turn-e2e",
        "claim_level": "reviewed",
        "declared_type": "image",
        "metadata": {"route": "image_edit"},
    })
    assert out["success"], f"artifact_write failed: {out}"

    receipt_args = {
        "source_ids": [src["artifact_id"]],
        "output_id": out["artifact_id"],
        "route": "image_edit",
        "status": "pass",
        "claim_level": "reviewed",
        "current_turn_id": "turn-e2e",
        "checks": [{"name": "lineage", "status": "pass"}],
        "user_visible_summary": "Render passed all fidelity gates.",
    }
    rcpt = call("receipt_record", receipt_args)
    assert rcpt["success"], f"receipt_record failed: {rcpt}"

    gate = call("delivery_gate", {"receipt_id": rcpt["receipt_id"]})
    assert gate["success"], f"delivery_gate failed: {gate}"
    assert gate["delivery_gate"] == "clear", f"expected clear, got {gate['delivery_gate']}"
    pdf_field = gate.get("receipt_pdf_path")
    assert pdf_field, f"receipt_pdf_path missing from gate response: {gate}"
    info = _assert_valid_pdf(Path(pdf_field))
    info["path"] = pdf_field
    info["gate"] = gate["delivery_gate"]
    return info


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="botji-receipt-pdf-") as tmp_str:
        tmp = Path(tmp_str)
        for name, fn in (("generator_direct", case_generator_direct),
                         ("wired_delivery_gate", case_wired_delivery_gate)):
            try:
                info = fn(tmp / name)
                print(f"[PASS] {name}: pdf={info['path']} size={info['size']}B pages={info['pages']} title={info.get('title')!r}")
            except AssertionError as exc:
                failures.append(f"{name}: {exc}")
                print(f"[FAIL] {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print(" -", f)
        return 1
    print("\nAll receipt PDF cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
