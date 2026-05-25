"""End-to-end C2PA stamping fixture — no mocks.

Generates a real 512x512 red square PNG with Pillow, signs it via the
``stamp_png`` entry on a real PS256 RSA-2048 self-signed cert (generated
by ``scripts/c2pa-gen-dev-cert.sh``), and asserts the output PNG carries a
valid C2PA manifest with the EU AI Act minimum claim set.

Validation strategy:

1. Check the raw PNG bytes for the ``jumb`` / ``caBX`` (JUMBF) chunk that
   signals an embedded C2PA manifest store.
2. Prefer ``c2patool`` if installed — it gives the gold-standard
   verification output (signature valid, TSA trusted, assertions parsed).
3. Fallback to ``c2pa.Reader`` (Python binding) for in-process assertion
   parsing; assert the EU AI Act minimum claim set is present:
     - c2pa.actions.v2 with c2pa.created
     - stds.iptc with AIGeneratedContent=true
     - cawg.training-mining opt-out entries

The fixture fails loudly if the cert + key aren't present — it does NOT
auto-generate them. Run ``scripts/c2pa-gen-dev-cert.sh`` first.

Run with::

    .venv/Scripts/python tests/fixtures/c2pa_stamp/test_c2pa_stamp.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Make the c2pa_stamp module importable. The plugin layout puts modules at
# seed/hermes/plugins/botji-render/<name>.py and they import siblings by
# bare name; add that directory to sys.path here.
ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / "seed" / "hermes" / "plugins" / "botji-render"
sys.path.insert(0, str(PLUGIN_DIR))

from c2pa_stamp import stamp_png  # noqa: E402

CERT_DIR = ROOT / "data" / ".c2pa"
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"


def _require_cert():
    if not CERT_PATH.is_file() or not KEY_PATH.is_file():
        raise SystemExit(
            f"ERROR: dev cert + key missing at {CERT_DIR}.\n"
            f"Run scripts/c2pa-gen-dev-cert.sh to generate them.\n"
            f"  cert_exists={CERT_PATH.is_file()} key_exists={KEY_PATH.is_file()}"
        )


def _make_red_square(path: Path) -> None:
    from PIL import Image
    img = Image.new("RGB", (512, 512), color=(220, 20, 20))
    img.save(path, format="PNG")


def _has_c2pa_chunk(png: Path) -> bool:
    """Look for the C2PA JUMBF chunk signature in raw bytes.

    PNGs embed C2PA inside an ancillary ``caBX`` (or ``jumb``) chunk; the
    chunk type appears verbatim in the file. Either signature counts.
    """
    data = png.read_bytes()
    return b"c2pa" in data or b"caBX" in data or b"jumb" in data


def _find_c2patool() -> str | None:
    return shutil.which("c2patool")


def _validate_with_c2patool(png: Path) -> tuple[bool, str]:
    tool = _find_c2patool()
    if not tool:
        return False, "c2patool not on PATH"
    res = subprocess.run(
        [tool, str(png), "--detailed"],
        capture_output=True, text=True, timeout=60,
    )
    out = (res.stdout or "") + "\n--- STDERR ---\n" + (res.stderr or "")
    if res.returncode != 0:
        return False, f"c2patool exit={res.returncode}\n{out}"
    return True, out


def _validate_with_python(png: Path) -> tuple[dict, dict]:
    """Read the manifest back via c2pa-python.

    Returns (active_manifest, full_store_json) — the full store is needed
    to read ``validation_results`` (signature + TSA success codes).
    """
    import c2pa
    with c2pa.Reader("image/png", open(png, "rb")) as r:
        active = r.get_active_manifest() or {}
        full = json.loads(r.json())
    return active, full


def main() -> int:
    _require_cert()

    os.environ["BOTJI_C2PA_CERT_PATH"] = str(CERT_PATH)
    os.environ["BOTJI_C2PA_KEY_PATH"] = str(KEY_PATH)

    with tempfile.TemporaryDirectory(prefix="c2pa_fixture_") as tmp:
        tmp_dir = Path(tmp)
        raw_png = tmp_dir / "raw_red_512.png"
        source_png = tmp_dir / "ingredient_source.png"
        out_png = tmp_dir / "raw_red_512_stamped.png"
        _make_red_square(raw_png)
        _make_red_square(source_png)
        print(f"[fixture] generated raw PNG: {raw_png} "
              f"({raw_png.stat().st_size} bytes)")

        summary = stamp_png(
            raw_png,
            out_png,
            botji_version="test-v1",
            source_image_paths=[source_png],
            user_prompt_redacted=(
                "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15"
                "d6c15b0f00a08 (redacted)"
            ),
            ai_model="gpt-image-2",
        )
        print(f"[fixture] stamp_png returned: {json.dumps(summary, indent=2)}")

        assert out_png.is_file(), f"output PNG not written: {out_png}"
        out_size = out_png.stat().st_size
        print(f"[fixture] output PNG size: {out_size} bytes "
              f"(input was {raw_png.stat().st_size})")
        assert out_size > raw_png.stat().st_size, (
            "stamped PNG should be larger than raw PNG (manifest adds bytes)"
        )

        # 1) Raw-byte chunk check.
        assert _has_c2pa_chunk(out_png), (
            "no C2PA / JUMBF chunk signature found in output PNG bytes"
        )
        print("[fixture] OK: C2PA JUMBF chunk present in PNG bytes")

        # 2) c2patool CLI validation (gold).
        tool_ok, tool_out = _validate_with_c2patool(out_png)
        if tool_ok:
            print("[fixture] c2patool validation PASSED")
            print("--- c2patool output ---")
            print(tool_out)
            assert "c2pa.actions.v2" in tool_out, (
                "c2patool output missing c2pa.actions.v2"
            )
            assert "c2pa.created" in tool_out, (
                "c2patool output missing c2pa.created action"
            )
            assert "stds.iptc" in tool_out, (
                "c2patool output missing stds.iptc assertion"
            )
            assert "AIGeneratedContent" in tool_out, (
                "c2patool output missing IPTC AIGeneratedContent field"
            )
        else:
            print(f"[fixture] c2patool unavailable / failed: {tool_out}")

        # 3) c2pa-python Reader validation (always run — fallback if no
        #    c2patool, sanity check if c2patool also ran).
        active, store = _validate_with_python(out_png)
        assert active, "Reader.get_active_manifest returned empty"
        labels = sorted(a.get("label", "") for a in active.get("assertions", []))
        print(f"[fixture] active manifest assertions: {labels}")

        # Signature & TSA success codes from validation_results.
        vresults = store.get("validation_results", {}).get(
            "activeManifest", {}).get("success", [])
        codes = sorted({v.get("code", "") for v in vresults})
        print(f"[fixture] validation success codes: {codes}")
        assert "claimSignature.validated" in codes, (
            f"missing claimSignature.validated; got {codes}"
        )
        assert "timeStamp.validated" in codes, (
            f"missing timeStamp.validated (RFC 3161 TSA proof); got {codes}"
        )
        print("[fixture] OK: signature + RFC 3161 timestamp both validated")

        sig_info = active.get("signature_info", {})
        assert sig_info.get("alg", "").lower() == "ps256", (
            f"signature_info.alg != ps256; got {sig_info}"
        )
        assert sig_info.get("time"), (
            f"signature_info missing TSA-issued time; got {sig_info}"
        )
        print(f"[fixture] OK: signature_info alg={sig_info.get('alg')} "
              f"time={sig_info.get('time')} CN={sig_info.get('common_name')}")

        def _get(lbl: str) -> dict:
            for a in active.get("assertions", []):
                if a.get("label") == lbl:
                    return a.get("data", {})
            raise AssertionError(f"missing assertion {lbl!r}; got {labels}")

        actions = _get("c2pa.actions.v2")
        action_names = [a.get("action") for a in actions.get("actions", [])]
        assert "c2pa.created" in action_names, (
            f"c2pa.actions.v2 missing c2pa.created; got {action_names}"
        )
        print("[fixture] OK: c2pa.actions.v2 contains c2pa.created")

        iptc = _get("stds.iptc")
        assert iptc.get("Iptc4xmpExt:AIGeneratedContent") is True, (
            f"stds.iptc missing AIGeneratedContent=true; got {iptc}"
        )
        print("[fixture] OK: stds.iptc declares AIGeneratedContent=true")

        tm = _get("cawg.training-mining")
        entries = tm.get("entries", {})
        assert entries.get("cawg.ai_generative_training", {}).get("use") \
            == "notAllowed", (
            f"cawg.training-mining missing ai_generative_training opt-out; got {tm}"
        )
        assert entries.get("cawg.ai_inference", {}).get("use") == "notAllowed", (
            f"cawg.training-mining missing ai_inference opt-out; got {tm}"
        )
        print("[fixture] OK: cawg.training-mining opt-outs present")

        print("\n[fixture] ALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
