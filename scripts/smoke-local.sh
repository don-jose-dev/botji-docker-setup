#!/usr/bin/env bash
set -euo pipefail

docker compose --env-file .env config >/dev/null
docker compose --env-file .env --profile bootstrap run --rm bootstrap >/dev/null

docker compose --env-file .env run --rm hermes bash -lc '
  set -euo pipefail
  codex --version
  hermes --help >/dev/null
  test -f /opt/data/SOUL.md
  test -f /opt/data/skills/botji-source-fidelity/SKILL.md
  test -f /opt/data/schemas/prompt_contract.schema.json
  test -f /opt/data/schemas/source_fidelity_review.schema.json
  test -f /opt/data/schemas/artifact_schema.schema.json
  test -f /opt/data/.codex/config.toml
  botji-validate-review /opt/data/skills/botji-source-fidelity/examples/review.pass.json
  botji-artifact-harness --strict --require-modality-comparators >/tmp/botji-artifact-harness.json
'

echo "Local smoke passed."
