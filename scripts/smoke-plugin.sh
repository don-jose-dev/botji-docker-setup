#!/usr/bin/env bash
# Smoke-test the botji-artifacts plugin inside the running container (or locally if
# PLUGIN_DIR is set). Exits non-zero on the first import or handler error.
#
# Usage (from repo root):
#   Local:     PLUGIN_DIR=seed/hermes/plugins/botji-artifacts bash scripts/smoke-plugin.sh
#   On VPS:    docker exec botji-hermes bash /opt/botji/scripts/smoke-plugin.sh
set -euo pipefail

PLUGIN_DIR="${PLUGIN_DIR:-/opt/data/plugins/botji-artifacts}"

echo "=== smoke-plugin: checking $PLUGIN_DIR ==="

python3 - <<PYEOF
import sys, os, importlib, traceback

plugin_dir = "${PLUGIN_DIR}"
if not os.path.isdir(plugin_dir):
    print(f"FAIL  plugin directory not found: {plugin_dir}", flush=True)
    sys.exit(1)

sys.path.insert(0, plugin_dir)

SUBMODULES = [
    "_constants",
    "_utils",
    "_detect",
    "_registry",
    "_extraction",
    "_rendering",
    "_normalization",
    "_codex",
    "_vision",
    "_review",
    "_handlers",
    "_schemas",
    "_prompts",
]

failures = []
for name in SUBMODULES:
    try:
        importlib.import_module(name)
        print(f"  ok  {name}", flush=True)
    except Exception as exc:
        print(f"  FAIL  {name}: {exc}", flush=True)
        traceback.print_exc()
        failures.append(name)

# Load the plugin entry point (registers tools via register())
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("botji_artifacts_plugin", os.path.join(plugin_dir, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("  ok  __init__ (plugin entry point)", flush=True)
except Exception as exc:
    print(f"  FAIL  __init__: {exc}", flush=True)
    traceback.print_exc()
    failures.append("__init__")

# Verify the key public symbols are importable
EXPECTED_SYMBOLS = {
    "_handlers": ["_handle_artifact_register", "_handle_artifact_transform",
                  "_handle_artifact_review", "_handle_artifact_normalize",
                  "_handle_artifact_extract", "_handle_artifact_list", "_handle_artifact_read"],
    "_schemas":  ["ARTIFACT_TRANSFORM_SCHEMA", "ARTIFACT_REVIEW_SCHEMA",
                  "ARTIFACT_REGISTER_SCHEMA", "ARTIFACT_NORMALIZE_SCHEMA"],
    "_review":   ["_build_review", "_reviews_dir"],
    "_rendering": ["_resolve_schema_payload", "_render_schema_preview_png",
                   "_create_output_artifact", "_load_evidence"],
    "_extraction": ["_build_normalized_schema", "_coerce_source_ids",
                    "_deterministic_schema_for_artifact"],
    "_constants": ["MAX_SOURCE_ARTIFACTS", "ARTIFACT_SCHEMA_VERSION",
                   "STRUCTURED_ADAPTERS"],
}
for mod_name, symbols in EXPECTED_SYMBOLS.items():
    try:
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        for sym in symbols:
            if not hasattr(mod, sym):
                msg = f"{mod_name}.{sym} not found"
                print(f"  FAIL  {msg}", flush=True)
                failures.append(msg)
            else:
                print(f"  ok  {mod_name}.{sym}", flush=True)
    except Exception as exc:
        failures.append(f"{mod_name} import: {exc}")

if failures:
    print(f"\nFAIL  {len(failures)} issue(s) found:", flush=True)
    for f in failures:
        print(f"  - {f}", flush=True)
    sys.exit(1)
else:
    print(f"\nPASS  all {len(SUBMODULES) + 1} submodules and {sum(len(v) for v in EXPECTED_SYMBOLS.values())} symbols OK", flush=True)
PYEOF

echo "=== smoke-plugin: PASSED ==="
