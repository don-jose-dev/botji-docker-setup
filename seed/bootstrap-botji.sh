#!/usr/bin/env sh
set -eu

DATA="${HERMES_HOME:-/opt/data}"
SEED="${BOTJI_SEED_DIR:-/seed}"
WORKSPACE="${BOTJI_WORKSPACE_DIR:-/workspace}"
FORCE="${BOTJI_FORCE_SEED:-0}"
TENANT="${BOTJI_TENANT_ID:-botji}"
OWNER="${HERMES_UID:-10000}:${HERMES_GID:-10000}"

mkdir -p \
  "$DATA" \
  "$DATA/skills" \
  "$DATA/plugins" \
  "$DATA/schemas" \
  "$DATA/artifacts" \
  "$DATA/reviews" \
  "$DATA/prompts" \
  "$DATA/logs" \
  "$DATA/verdicts" \
  "$DATA/.codex" \
  "$DATA/.botji" \
  "$WORKSPACE"

chown "$OWNER" \
  "$DATA" \
  "$DATA/skills" \
  "$DATA/plugins" \
  "$DATA/schemas" \
  "$DATA/artifacts" \
  "$DATA/reviews" \
  "$DATA/prompts" \
  "$DATA/logs" \
  "$DATA/verdicts" \
  "$DATA/.codex" \
  "$DATA/.botji" \
  "$WORKSPACE" 2>/dev/null || true

copy_file() {
  src="$1"
  dst="$2"
  if [ "$FORCE" = "1" ] || [ ! -f "$dst" ]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chown "$OWNER" "$dst" 2>/dev/null || true
    echo "seeded file: $dst"
  else
    chown "$OWNER" "$dst" 2>/dev/null || true
    echo "kept existing file: $dst"
  fi
}

copy_dir() {
  src="$1"
  dst="$2"
  if [ "$FORCE" = "1" ]; then
    rm -rf "$dst"
  fi
  if [ ! -d "$dst" ]; then
    mkdir -p "$(dirname "$dst")"
    cp -R "$src" "$dst"
    chown -R "$OWNER" "$dst" 2>/dev/null || true
    echo "seeded dir: $dst"
  else
    chown -R "$OWNER" "$dst" 2>/dev/null || true
    echo "kept existing dir: $dst"
  fi
}

copy_file "$SEED/hermes/SOUL.md" "$DATA/SOUL.md"
if [ -f "$SEED/../BOTJI_V1.md" ]; then
  copy_file "$SEED/../BOTJI_V1.md" "$DATA/BOTJI_V1.md"
fi
for _skill_src in "$SEED/hermes/skills"/botji-*/; do
  [ -d "$_skill_src" ] || continue
  _skill_name="$(basename "$_skill_src")"
  copy_dir "$_skill_src" "$DATA/skills/$_skill_name"
done
copy_file "$SEED/hermes/config.yaml" "$DATA/config.yaml"
# seed/hermes/prompts/ is optional. Runtime prompts now live under each plugin
# (e.g. botji-artifacts/prompts/) and are loaded via _prompts.load_prompt().
if [ -d "$SEED/hermes/prompts" ]; then
  copy_dir "$SEED/hermes/prompts" "$DATA/prompts"
fi
for _plugin_src in "$SEED/hermes/plugins"/botji-*/; do
  [ -d "$_plugin_src" ] || continue
  [ -f "$_plugin_src/plugin.yaml" ] && [ -f "$_plugin_src/__init__.py" ] || continue
  _plugin_name="$(basename "$_plugin_src")"
  copy_dir "$_plugin_src" "$DATA/plugins/$_plugin_name"
done

copy_file "$SEED/hermes/schemas/prompt_contract.schema.json" "$DATA/schemas/prompt_contract.schema.json"
copy_file "$SEED/hermes/schemas/source_fidelity_review.schema.json" "$DATA/schemas/source_fidelity_review.schema.json"
copy_file "$SEED/hermes/schemas/artifact_lineage.schema.json" "$DATA/schemas/artifact_lineage.schema.json"
copy_file "$SEED/hermes/schemas/artifact_schema.schema.json" "$DATA/schemas/artifact_schema.schema.json"
copy_file "$SEED/hermes/schemas/manifest_v2.schema.json" "$DATA/schemas/manifest_v2.schema.json"

copy_file "$SEED/codex/config.toml" "$DATA/.codex/config.toml"
chmod 600 "$DATA/.codex/config.toml" 2>/dev/null || true

# Workspace writes are best-effort — the directory may be owned by root on first boot.
set +e
copy_file "$SEED/codex/AGENTS.md" "$WORKSPACE/AGENTS.md"
copy_file "$SEED/workspace/README.md" "$WORKSPACE/README.md"
set -e

cat > "$DATA/.botji/BOOTSTRAP_RECEIPT.md" <<EOF
# Botji bootstrap receipt

Tenant: ${TENANT}
Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Hermes home: ${DATA}
Workspace: ${WORKSPACE}

Seeded controls:
- SOUL.md
- config.yaml (streaming, models, display)
- prompt-contract skill
- source-fidelity skill
- codex-engineering skill
- artifact-fidelity skill
- 2d-to-3d skill
- prompt templates
- botji-* plugins (botji-artifacts, botji-render, botji-allowlist, botji-gate)
- JSON schemas
- Codex config profiles
- workspace AGENTS.md
EOF

chown "$OWNER" "$DATA/.botji/BOOTSTRAP_RECEIPT.md" 2>/dev/null || true

echo "Botji seed complete. Receipt: $DATA/.botji/BOOTSTRAP_RECEIPT.md"
