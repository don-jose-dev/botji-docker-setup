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
copy_dir "$SEED/hermes/skills/botji-prompt-contract" "$DATA/skills/botji-prompt-contract"
copy_dir "$SEED/hermes/skills/botji-source-fidelity" "$DATA/skills/botji-source-fidelity"
copy_dir "$SEED/hermes/skills/botji-codex-engineering" "$DATA/skills/botji-codex-engineering"
copy_dir "$SEED/hermes/skills/botji-artifact-fidelity" "$DATA/skills/botji-artifact-fidelity"
copy_dir "$SEED/hermes/skills/botji-2d-to-3d" "$DATA/skills/botji-2d-to-3d"
copy_file "$SEED/hermes/config.yaml" "$DATA/config.yaml"
copy_dir "$SEED/hermes/prompts" "$DATA/prompts"
copy_dir "$SEED/hermes/plugins/botji-artifacts" "$DATA/plugins/botji-artifacts"

copy_file "$SEED/hermes/schemas/prompt_contract.schema.json" "$DATA/schemas/prompt_contract.schema.json"
copy_file "$SEED/hermes/schemas/source_fidelity_review.schema.json" "$DATA/schemas/source_fidelity_review.schema.json"
copy_file "$SEED/hermes/schemas/artifact_lineage.schema.json" "$DATA/schemas/artifact_lineage.schema.json"
copy_file "$SEED/hermes/schemas/artifact_schema.schema.json" "$DATA/schemas/artifact_schema.schema.json"

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
- artifact registry plugin
- JSON schemas
- Codex config profiles
- workspace AGENTS.md
EOF

chown "$OWNER" "$DATA/.botji/BOOTSTRAP_RECEIPT.md" 2>/dev/null || true

echo "Botji seed complete. Receipt: $DATA/.botji/BOOTSTRAP_RECEIPT.md"
