"""Dispatch-level integration: ensure botji-render.dispatch() stamps PNG outputs.

Calls ``operations.dispatch("exact_copy", ...)`` with a PNG source and
asserts the returned RenderResult has ``metadata['c2pa_stamped'] = True``
plus a ``c2pa`` summary dict — i.e. the post-op stamping hook ran.

Also verifies the disable knob: when ``BOTJI_C2PA_ENABLED=0`` the result
PNG passes through unstamped and ``metadata['c2pa_stamped'] = False``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / "seed" / "hermes" / "plugins" / "botji-render"
sys.path.insert(0, str(PLUGIN_DIR))

from operations import RenderPolicy, dispatch  # noqa: E402

CERT_DIR = ROOT / "data" / ".c2pa"
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"


def _make_red_png(path: Path) -> None:
    from PIL import Image
    Image.new("RGB", (256, 256), (220, 20, 20)).save(path, format="PNG")


def main() -> int:
    if not CERT_PATH.is_file() or not KEY_PATH.is_file():
        raise SystemExit(
            f"ERROR: cert + key missing at {CERT_DIR}. "
            f"Run scripts/c2pa-gen-dev-cert.sh first."
        )
    os.environ["BOTJI_C2PA_CERT_PATH"] = str(CERT_PATH)
    os.environ["BOTJI_C2PA_KEY_PATH"] = str(KEY_PATH)

    with tempfile.TemporaryDirectory(prefix="c2pa_dispatch_") as tmp:
        tmp_dir = Path(tmp)
        src_png = tmp_dir / "src.png"
        _make_red_png(src_png)

        # Path 1: default — stamping enabled.
        os.environ["BOTJI_C2PA_ENABLED"] = "1"
        result = dispatch(
            "exact_copy",
            [src_png],
            RenderPolicy(route="exact_copy"),
            output_dir=tmp_dir / "out1",
            botji_version="dispatch-test-v1",
            user_prompt_redacted="sha256:test",
        )
        print(f"[enabled] success={result.success} "
              f"c2pa_stamped={result.metadata.get('c2pa_stamped')}")
        print(f"[enabled] c2pa summary: "
              f"{json.dumps(result.metadata.get('c2pa', {}), indent=2)}")
        assert result.success, f"dispatch failed: {result.error}"
        assert result.metadata.get("c2pa_stamped") is True, (
            f"expected c2pa_stamped=True; got metadata={result.metadata}"
        )
        c2pa_summary = result.metadata.get("c2pa", {})
        assert c2pa_summary.get("manifest_id"), (
            f"missing manifest_id in c2pa summary: {c2pa_summary}"
        )
        assert c2pa_summary.get("signing_alg") == "ps256"
        # Confirm the file on disk is actually stamped (not the raw source).
        out_bytes = result.output_path.read_bytes()
        assert b"c2pa" in out_bytes or b"jumb" in out_bytes, (
            "stamped PNG missing C2PA chunk signature"
        )
        print("[enabled] OK: dispatch stamped the PNG in place")

        # Path 2: disable via env — no stamping, raw bytes pass through.
        os.environ["BOTJI_C2PA_ENABLED"] = "0"
        result2 = dispatch(
            "exact_copy",
            [src_png],
            RenderPolicy(route="exact_copy"),
            output_dir=tmp_dir / "out2",
            botji_version="dispatch-test-v1",
        )
        print(f"[disabled] success={result2.success} "
              f"c2pa_stamped={result2.metadata.get('c2pa_stamped')} "
              f"skip_reason={result2.metadata.get('c2pa_skip_reason')}")
        assert result2.success
        assert result2.metadata.get("c2pa_stamped") is False
        assert result2.metadata.get("c2pa_skip_reason") == \
            "BOTJI_C2PA_ENABLED disabled"
        raw2 = result2.output_path.read_bytes()
        assert b"caBX" not in raw2 and b"jumb" not in raw2, (
            "PNG should NOT carry C2PA chunk when BOTJI_C2PA_ENABLED=0"
        )
        print("[disabled] OK: stamping skipped, raw PNG passed through")

        # Path 3: enabled but cert missing — soft failure path.
        os.environ["BOTJI_C2PA_ENABLED"] = "1"
        os.environ["BOTJI_C2PA_CERT_PATH"] = str(tmp_dir / "no-such-cert.pem")
        result3 = dispatch(
            "exact_copy",
            [src_png],
            RenderPolicy(route="exact_copy"),
            output_dir=tmp_dir / "out3",
            botji_version="dispatch-test-v1",
        )
        print(f"[bad-cert] success={result3.success} "
              f"c2pa_stamped={result3.metadata.get('c2pa_stamped')} "
              f"error={result3.metadata.get('c2pa_error')}")
        assert result3.success, "dispatch must NOT fail when stamping fails"
        assert result3.metadata.get("c2pa_stamped") is False
        assert "c2pa_error" in result3.metadata
        print("[bad-cert] OK: render succeeded with c2pa_stamped=False (soft fail)")

    print("\n[dispatch-integration] ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
