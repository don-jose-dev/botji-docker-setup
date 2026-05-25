#!/usr/bin/env bash
# Smoke-test every botji plugin inside the running container (or locally if
# PLUGINS_ROOT is set). Exits non-zero on the first import or handler error.
#
# Usage (from repo root):
#   Local:     PLUGINS_ROOT=seed/hermes/plugins bash scripts/smoke-plugin.sh
#   On VPS:    docker exec botji-hermes bash /opt/botji/scripts/smoke-plugin.sh
#
# Backwards compatible: if PLUGIN_DIR is set (legacy single-plugin mode),
# only that plugin is checked. PLUGINS_ROOT takes precedence when both set.
set -euo pipefail

PLUGINS_ROOT="${PLUGINS_ROOT:-${PLUGIN_DIR_PARENT:-/opt/data/plugins}}"
# Legacy single-plugin support: if PLUGIN_DIR points at one plugin dir, derive
# the root and the target plugin name so old invocations still work.
LEGACY_PLUGIN="${PLUGIN_DIR:-}"

echo "=== smoke-plugin: checking $PLUGINS_ROOT ==="

python3 - <<PYEOF
import sys, os, importlib, importlib.util, traceback

plugins_root = "${PLUGINS_ROOT}"
legacy_plugin = "${LEGACY_PLUGIN}"

# Per-plugin contract: list of submodules that must import + entry symbols
# that must be present after register(). Adding a new plugin is just one
# entry here.
PLUGIN_CONTRACTS = {
    "botji-artifacts": {
        "submodules": [
            "_constants", "_utils", "_detect", "_registry", "_metadata",
            "_extraction", "_rendering", "_normalization", "_codex", "_vision",
            "_review", "_models", "_handlers", "_schemas", "_prompts",
        ],
        "symbols": {
            "_handlers": ["_handle_artifact_register", "_handle_artifact_transform",
                          "_handle_artifact_review", "_handle_artifact_normalize",
                          "_handle_artifact_extract", "_handle_artifact_extract_manifest",
                          "_handle_artifact_list", "_handle_artifact_read"],
            "_schemas":  ["ARTIFACT_TRANSFORM_SCHEMA", "ARTIFACT_REVIEW_SCHEMA",
                          "ARTIFACT_REGISTER_SCHEMA", "ARTIFACT_NORMALIZE_SCHEMA",
                          "ARTIFACT_EXTRACT_MANIFEST_SCHEMA"],
            "_review":   ["_build_review", "_reviews_dir"],
            "_rendering": ["_resolve_schema_payload", "_render_schema_preview_png",
                           "_create_output_artifact", "_load_evidence"],
            "_extraction": ["_build_normalized_schema", "_coerce_source_ids",
                            "_deterministic_schema_for_artifact"],
            "_constants": ["MAX_SOURCE_ARTIFACTS", "ARTIFACT_SCHEMA_VERSION",
                           "STRUCTURED_ADAPTERS"],
        },
    },
    "botji-render": {
        # V1R PR 9b: operations + real Codex image provider.
        # v1.1: c2pa_stamp.py adds EU AI Act Article 50 manifest stamping for
        # PNG outputs; operations.dispatch wraps every PNG result with it.
        "submodules": ["operations", "providers.openai_codex", "c2pa_stamp"],
        "symbols": {
            "operations": ["dispatch", "exact_copy", "render_schema", "edit_image",
                           "OPERATIONS", "RenderPolicy", "RenderResult"],
            "providers.openai_codex": ["generate_image", "resolve_provider_route", "codex_available"],
            "c2pa_stamp": ["stamp_png"],
        },
    },
    "botji-allowlist": {
        "submodules": [],   # logic lives in __init__.py
        "symbols": {},
    },
    "botji-gate": {
        "submodules": [],
        "symbols": {},
    },
    "botji-core": {
        "submodules": [],
        "symbols": {},
    },
    "botji-catalog": {
        "submodules": [],
        "symbols": {
            "__init__": ["catalog_check", "catalog_load", "catalog_update"],
        },
    },
    "botji-receipt": {
        # PDF receipt plugin. __init__ probes fpdf2 lazily so the smoke test
        # does not fail in environments where the dep is not yet installed.
        "submodules": [],
        "symbols": {},
    },
}
REQUIRED_PLUGINS = {"botji-allowlist", "botji-artifacts", "botji-catalog", "botji-core", "botji-gate", "botji-receipt", "botji-render"}

# Resolve which plugins to check: if PLUGINS_ROOT is set use it (preferred),
# else fall back to a single legacy PLUGIN_DIR for back-compat.
legacy_mode = False
if legacy_plugin and not os.path.isdir(plugins_root):
    plugin_dirs = [legacy_plugin]
    legacy_mode = True
else:
    if not os.path.isdir(plugins_root):
        print(f"FAIL  plugins root not found: {plugins_root}", flush=True)
        sys.exit(1)
    plugin_dirs = [
        os.path.join(plugins_root, d)
        for d in sorted(os.listdir(plugins_root))
        if (
            d in PLUGIN_CONTRACTS
            and os.path.isdir(os.path.join(plugins_root, d))
            and os.path.isfile(os.path.join(plugins_root, d, "__init__.py"))
            and os.path.isfile(os.path.join(plugins_root, d, "plugin.yaml"))
        )
    ]

failures = []
total_submodules = 0
total_symbols = 0
present_plugins = {os.path.basename(path.rstrip("/")) for path in plugin_dirs}
missing_required = [] if legacy_mode else sorted(REQUIRED_PLUGINS - present_plugins)
if missing_required:
    failures.extend(f"missing required plugin: {name}" for name in missing_required)

for plugin_dir in plugin_dirs:
    plugin_name = os.path.basename(plugin_dir.rstrip("/"))
    contract = PLUGIN_CONTRACTS.get(plugin_name)
    if contract is None:
        print(f"  SKIP  {plugin_name} (no contract)", flush=True)
        continue
    print(f"--- {plugin_name} ---", flush=True)

    # Each plugin gets its own clean sys.path entry; remove any prior plugins'
    # private modules from sys.modules so name clashes (_handlers, etc.) can't
    # cross-contaminate.
    for other in list(sys.modules):
        if other.startswith("_") and other in {m for c in PLUGIN_CONTRACTS.values() for m in c["submodules"]}:
            sys.modules.pop(other, None)
    sys.path = [p for p in sys.path if not p.endswith(tuple(PLUGIN_CONTRACTS))]
    sys.path.insert(0, plugin_dir)

    for name in contract["submodules"]:
        total_submodules += 1
        try:
            mod = importlib.import_module(name)
            # Stdlib-collision guard: a submodule like _queue.py can be shadowed
            # by Python's built-in _queue C extension because the stdlib's
            # sys.modules cache hits first. Verify the loaded module's __file__
            # actually points at our plugin directory.
            mod_file = getattr(mod, "__file__", "") or ""
            expected_prefix = os.path.realpath(plugin_dir)
            if mod_file and not os.path.realpath(mod_file).startswith(expected_prefix):
                raise ImportError(
                    f"name '{name}' collided with stdlib/site-packages module at "
                    f"{mod_file!s}; rename the plugin submodule to a unique name."
                )
            print(f"  ok  {plugin_name}/{name}", flush=True)
        except Exception as exc:
            print(f"  FAIL  {plugin_name}/{name}: {exc}", flush=True)
            traceback.print_exc()
            failures.append(f"{plugin_name}/{name}")

    init_path = os.path.join(plugin_dir, "__init__.py")
    if os.path.exists(init_path):
        try:
            spec = importlib.util.spec_from_file_location(f"{plugin_name}_pkg", init_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "register"):
                raise AttributeError("plugin missing required register() function")
            print(f"  ok  {plugin_name}/__init__ (register found)", flush=True)
        except Exception as exc:
            print(f"  FAIL  {plugin_name}/__init__: {exc}", flush=True)
            traceback.print_exc()
            failures.append(f"{plugin_name}/__init__")

    for mod_name, symbols in contract["symbols"].items():
        try:
            mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
            for sym in symbols:
                total_symbols += 1
                if not hasattr(mod, sym):
                    msg = f"{plugin_name}/{mod_name}.{sym} not found"
                    print(f"  FAIL  {msg}", flush=True)
                    failures.append(msg)
                else:
                    print(f"  ok  {plugin_name}/{mod_name}.{sym}", flush=True)
        except Exception as exc:
            failures.append(f"{plugin_name}/{mod_name} import: {exc}")

if failures:
    print(f"\nFAIL  {len(failures)} issue(s) across {len(plugin_dirs)} plugin(s):", flush=True)
    for f in failures:
        print(f"  - {f}", flush=True)
    sys.exit(1)
else:
    print(f"\nPASS  {len(plugin_dirs)} plugin(s), {total_submodules} submodules, {total_symbols} symbols OK", flush=True)
PYEOF

echo "=== smoke-plugin: PASSED ==="
