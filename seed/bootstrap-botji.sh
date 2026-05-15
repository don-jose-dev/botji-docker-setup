#!/usr/bin/env sh
set -eu

DATA="${HERMES_HOME:-/opt/data}"
SEED="${BOTJI_SEED_DIR:-/seed}"
WORKSPACE="${BOTJI_WORKSPACE_DIR:-/workspace}"
FORCE="${BOTJI_FORCE_SEED:-0}"
TENANT="${BOTJI_TENANT_ID:-botji}"

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

copy_file() {
  src="$1"
  dst="$2"
  if [ "$FORCE" = "1" ] || [ ! -f "$dst" ]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    echo "seeded file: $dst"
  else
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
    echo "seeded dir: $dst"
  else
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
copy_dir "$SEED/hermes/skills/botji-image-fidelity" "$DATA/skills/botji-image-fidelity"
copy_dir "$SEED/hermes/skills/botji-artifact-fidelity" "$DATA/skills/botji-artifact-fidelity"
copy_dir "$SEED/hermes/plugins/botji-image-fidelity" "$DATA/plugins/botji-image-fidelity"
copy_dir "$SEED/hermes/plugins/botji-artifacts" "$DATA/plugins/botji-artifacts"

copy_file "$SEED/hermes/schemas/prompt_contract.schema.json" "$DATA/schemas/prompt_contract.schema.json"
copy_file "$SEED/hermes/schemas/source_fidelity_review.schema.json" "$DATA/schemas/source_fidelity_review.schema.json"
copy_file "$SEED/hermes/schemas/artifact_lineage.schema.json" "$DATA/schemas/artifact_lineage.schema.json"
copy_file "$SEED/hermes/schemas/artifact_schema.schema.json" "$DATA/schemas/artifact_schema.schema.json"

copy_file "$SEED/codex/config.toml" "$DATA/.codex/config.toml"
copy_file "$SEED/codex/AGENTS.md" "$WORKSPACE/AGENTS.md"
copy_file "$SEED/workspace/README.md" "$WORKSPACE/README.md"

cat > "$DATA/.botji/BOOTSTRAP_RECEIPT.md" <<EOF
# Botji bootstrap receipt

Tenant: ${TENANT}
Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Hermes home: ${DATA}
Workspace: ${WORKSPACE}

Seeded controls:
- SOUL.md
- prompt-contract skill
- source-fidelity skill
- codex-engineering skill
- artifact-fidelity skill
- image-fidelity plugin
- artifact registry plugin
- JSON schemas
- artifact schema v1
- Codex config profiles
- workspace AGENTS.md
EOF

echo "Botji seed complete. Receipt: $DATA/.botji/BOOTSTRAP_RECEIPT.md"
