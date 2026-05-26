from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "seed" / "hermes" / "plugins" / "botji-artifacts"


def test_render_runtime_ignores_preloaded_upstream_providers(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    providers_dir = upstream / "providers"
    providers_dir.mkdir(parents=True)
    (providers_dir / "__init__.py").write_text("# upstream providers package\n", encoding="utf-8")

    monkeypatch.syspath_prepend(str(upstream))
    providers = importlib.import_module("providers")
    assert str(providers.__file__).startswith(str(providers_dir))

    monkeypatch.syspath_prepend(str(ARTIFACTS_DIR))
    for name in ("_handlers", "botji_render_operations", "botji_render_openai_codex"):
        sys.modules.pop(name, None)
    handlers = importlib.import_module("_handlers")

    RenderPolicy, dispatch, resolve_provider_route = handlers._render_runtime()
    assert resolve_provider_route("openai_codex") == "openai_codex"

    provider = sys.modules["botji_render_openai_codex"]
    operations = sys.modules["botji_render_operations"]

    def fake_generate_image(sources, policy, *, output_dir, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / "render.txt"
        out.write_text("ok", encoding="utf-8")
        return operations.RenderResult("edit_image", out, {"route": "fake"})

    monkeypatch.setattr(provider, "generate_image", fake_generate_image)
    result = dispatch("edit_image", [], RenderPolicy(), output_dir=tmp_path / "out")

    assert result.success
    assert result.output_path is not None
    assert result.output_path.read_text(encoding="utf-8") == "ok"
