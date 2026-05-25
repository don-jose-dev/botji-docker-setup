"""Real tests for the botji-catalog plugin (NO MOCKS).

Loads the plugin module by file path (same way runtime/bin/botji-harness does),
points BOTJI_CATALOG_PATH at fixture / tmp files, asserts on real return values.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_INIT = REPO_ROOT / "seed" / "hermes" / "plugins" / "botji-catalog" / "__init__.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "catalog_lock"


def _load_plugin():
    name = f"botji_catalog_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_INIT)
    assert spec and spec.loader, f"cannot import {PLUGIN_INIT}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8").strip()


@pytest.fixture
def catalog_env(tmp_path, monkeypatch):
    target = tmp_path / "catalog.json"
    target.write_text((FIXTURE_DIR / "catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("BOTJI_CATALOG_PATH", str(target))
    return target


def test_brief_a_passes(catalog_env):
    v = _load_plugin().catalog_check(_read("brief_a_pass.txt"))
    assert v["status"] == "pass", v
    assert v["banned_hits"] == []
    assert v["suggested_substitutions"] == {}
    for t in ("rift-sawn white oak", "Calacatta Nuvo", "brushed brass"):
        assert t in v["catalog_terms_used"]


def test_brief_b_blocks(catalog_env):
    v = _load_plugin().catalog_check(_read("brief_b_block.txt"))
    assert v["status"] == "block", v
    assert sorted(v["banned_hits"]) == sorted(["modern", "luxury", "sleek", "elegant"])
    subs = v["suggested_substitutions"]
    assert subs.get("wood") == "rift-sawn white oak", subs
    assert subs.get("marble") == "Calacatta Nuvo", subs


def test_brief_c_warns(catalog_env):
    v = _load_plugin().catalog_check(_read("brief_c_warn.txt"))
    assert v["status"] == "warn", v
    assert v["banned_hits"] == []
    assert v["suggested_substitutions"].get("wood") == "rift-sawn white oak"
    assert "Calacatta Nuvo" in v["catalog_terms_used"]
    assert "stone" not in v["suggested_substitutions"]  # catalog stone already present


def test_catalog_load_returns_defaults_when_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("BOTJI_CATALOG_PATH", str(missing))
    assert not missing.exists()
    cat = _load_plugin().catalog_load()
    assert cat["tenant_id"] == "botji"
    assert "rift-sawn white oak" in cat["materials"]["woods"]
    assert "luxury" in cat["banned_terms"]


def test_catalog_update_appends_and_persists(catalog_env):
    p = _load_plugin()
    result = p.catalog_update(field="woods", value=["bog oak", "ebonised ash"])
    assert result["success"] is True, result
    assert result["appended"] == ["bog oak", "ebonised ash"]
    on_disk = json.loads(catalog_env.read_text(encoding="utf-8"))
    assert "bog oak" in on_disk["materials"]["woods"]
    assert "ebonised ash" in on_disk["materials"]["woods"]
    again = p.catalog_update(field="woods", value=["bog oak"])  # idempotent
    assert again["success"] is True and again["appended"] == []
    assert "bog oak" in p.catalog_load()["materials"]["woods"]


def test_catalog_update_rejects_bad_input(catalog_env):
    p = _load_plugin()
    assert p.catalog_update(field="cheese", value=["brie"])["success"] is False
    assert p.catalog_update(field="woods", value=[])["success"] is False


def test_malformed_json_falls_back(tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{this is not json", encoding="utf-8")
    monkeypatch.setenv("BOTJI_CATALOG_PATH", str(bad))
    cat = _load_plugin().catalog_load()
    assert cat["tenant_id"] == "botji"
    assert "luxury" in cat["banned_terms"]


def test_hot_reload_on_mtime_change(catalog_env):
    p = _load_plugin()
    assert "bog oak" not in p.catalog_load()["materials"].get("woods", [])
    data = json.loads(catalog_env.read_text(encoding="utf-8"))
    data["materials"]["woods"].append("bog oak")
    catalog_env.write_text(json.dumps(data), encoding="utf-8")
    os.utime(catalog_env, (catalog_env.stat().st_atime, catalog_env.stat().st_mtime + 2))
    assert "bog oak" in p.catalog_load()["materials"]["woods"]
