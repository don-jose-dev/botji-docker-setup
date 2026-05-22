#!/usr/bin/env bash
# deploy-tenants.sh — primary tenant compose, additional tenants, rollback,
# and the code components sync used by both. Sourced by deploy.sh. Defines:
#   sync_code_components       — reseed plugins/skills/schemas/prompts under
#                                $DATA_DIR (idempotent; honours per-tenant
#                                overrides of DATA_DIR / HERMES_RUNTIME_UID /
#                                HERMES_RUNTIME_GID so it can be reused by
#                                deploy_additional_tenant).
#   rollback_to_previous STATUS — revert to $PREVIOUS_GIT_HEAD /
#                                $PREVIOUS_IMAGE on failure (sets
#                                ROLLBACK_DONE=1).
#   bootstrap_workspace        — workspace dir prep + idempotent bootstrap
#                                compose profile run.
#   start_primary_tenant       — defensive name reclaim + compose up the
#                                primary tenant container.
#   record_last_deploy STATUS  — write $DATA_DIR/.botji/LAST_DEPLOY.json.
#   deploy_additional_tenant PATH — sync + compose up a sibling tenant
#                                (non-fatal on failure).
#   run_additional_tenants     — iterate over $BOTJI_ADDITIONAL_TENANTS.
#
# Reads from setup_env: HERMES_RUNTIME_UID, HERMES_RUNTIME_GID, DATA_DIR,
# WORKSPACE_DIR. Reads from main: IMAGE_REF, PREVIOUS_GIT_HEAD,
# PREVIOUS_IMAGE, BOTJI_ADDITIONAL_TENANTS.

sync_code_components() {
  # Code artefacts — always reseed from the checked-out release. The glob-based
  # approach means new plugins/skills ship automatically without editing this script.
  mkdir -p "$DATA_DIR/skills" "$DATA_DIR/plugins" "$DATA_DIR/prompts" "$DATA_DIR/schemas"
  for skill_dir in seed/hermes/skills/botji-*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    rm -rf "$DATA_DIR/skills/$skill_name"
    cp -R "$skill_dir" "$DATA_DIR/skills/" 2>/dev/null || true
  done
  for plugin_dir in seed/hermes/plugins/botji-*; do
    [ -d "$plugin_dir" ] || continue
    [ -f "$plugin_dir/plugin.yaml" ] && [ -f "$plugin_dir/__init__.py" ] || continue
    plugin_name="$(basename "$plugin_dir")"
    valid_plugins="${valid_plugins:-} $plugin_name"
    rm -rf "$DATA_DIR/plugins/$plugin_name"
    cp -R "$plugin_dir" "$DATA_DIR/plugins/"
    echo "    seeded plugin: $plugin_name"
  done
  for deployed_plugin in "$DATA_DIR"/plugins/botji-*; do
    [ -d "$deployed_plugin" ] || continue
    plugin_name="$(basename "$deployed_plugin")"
    case " ${valid_plugins:-} " in
      *" $plugin_name "*) ;;
      *)
        rm -rf "$deployed_plugin"
        echo "    removed stale plugin: $plugin_name"
        ;;
    esac
  done
  # seed/hermes/prompts/ is optional — runtime prompts now live under each plugin
  # (botji-artifacts/prompts/*.md) and are loaded via _prompts.load_prompt(). The
  # top-level prompts/templates/ tree was unused and removed 2026-05-21. Keep the
  # data dir clean for any tenants that still have stale templates copied over.
  rm -rf "$DATA_DIR/prompts"
  if [ -d seed/hermes/prompts ]; then
    cp -R seed/hermes/prompts "$DATA_DIR/"
  fi
  cp seed/hermes/schemas/*.json "$DATA_DIR/schemas/" 2>/dev/null || true
  # kanban/workflows/ — declarative Kanban workflow templates (see
  # seed/hermes/kanban/README.md). The v1 kernel writes
  # workflow_template_id + current_step_key as forward-compat columns; the
  # skill-following agent reads these files today as the canonical
  # multi-step shape for durable retries. Lands at $DATA_DIR/kanban/.
  if [ -d seed/hermes/kanban ]; then
    rm -rf "$DATA_DIR/kanban"
    mkdir -p "$DATA_DIR/kanban"
    cp -R seed/hermes/kanban/. "$DATA_DIR/kanban/"
    echo "    seeded kanban workflows: $(find "$DATA_DIR/kanban/workflows" -name '*.yaml' 2>/dev/null | wc -l) template(s)"
  fi
  # cron/ — declarative Hermes cron seeds (see cron/README.md). Job yaml
  # lands in $DATA_DIR/cron/ and the matching scripts in $DATA_DIR/scripts/
  # because Hermes resolves cron `script:` paths against $HERMES_HOME/scripts/.
  # Files matching *.disabled are intentionally skipped so an operator can
  # disable a job by renaming its yaml without editing the repo.
  if [ -d cron ]; then
    mkdir -p "$DATA_DIR/cron" "$DATA_DIR/scripts"
    rm -f "$DATA_DIR"/cron/*.yaml 2>/dev/null || true
    for cron_yaml in cron/*.yaml; do
      [ -f "$cron_yaml" ] || continue
      case "$cron_yaml" in *.disabled.yaml|*.yaml.disabled) continue ;; esac
      cp "$cron_yaml" "$DATA_DIR/cron/"
      echo "    seeded cron: $(basename "$cron_yaml")"
    done
    if [ -d cron/scripts ]; then
      for cron_script in cron/scripts/*; do
        [ -f "$cron_script" ] || continue
        cp "$cron_script" "$DATA_DIR/scripts/"
        chmod +x "$DATA_DIR/scripts/$(basename "$cron_script")" 2>/dev/null || true
      done
    fi
  fi
  chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" \
    "$DATA_DIR/plugins" "$DATA_DIR/skills" "$DATA_DIR/prompts" "$DATA_DIR/schemas" 2>/dev/null || true
  [ -d "$DATA_DIR/kanban" ] && chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/kanban" 2>/dev/null || true
  [ -d "$DATA_DIR/cron" ] && chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/cron" 2>/dev/null || true
  [ -d "$DATA_DIR/scripts" ] && chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/scripts" 2>/dev/null || true
  mkdir -p "$DATA_DIR/verdicts"
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/verdicts" 2>/dev/null || true
}

rollback_to_previous() {
  # shellcheck disable=SC2034  # read by the cleanup trap in vps-deploy.sh
  ROLLBACK_DONE=1
  local failed_status="$1"
  echo "==> Rolling back after failed deploy ($failed_status)" >&2

  if [ -n "${PREVIOUS_GIT_HEAD:-}" ]; then
    git reset --hard "$PREVIOUS_GIT_HEAD" || true
    sync_code_components || true
    echo "    Repo and code components restored to $PREVIOUS_GIT_HEAD" >&2
  fi

  if [ -n "${PREVIOUS_IMAGE:-}" ]; then
    export BOTJI_PROD_IMAGE="$PREVIOUS_IMAGE"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
      up -d --force-recreate --no-build --remove-orphans || true
    for _ in $(seq 1 15); do
      ROLLBACK_STATUS=$(docker inspect botji-hermes \
        --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
      [ "$ROLLBACK_STATUS" = "healthy" ] && break
      sleep 4
    done
    echo "    Rollback status: ${ROLLBACK_STATUS:-unknown}" >&2
    [ "${ROLLBACK_STATUS:-}" = "healthy" ] || docker logs botji-hermes --tail 40 >&2 || true
  else
    echo "    No previous image available; manual intervention required" >&2
  fi
}

bootstrap_workspace() {
  echo "==> Bootstrap seed (idempotent — skips existing files)"
  export BOTJI_PROD_IMAGE="$IMAGE_REF"
  # Ensure workspace is writable by the hermes user (UID 10000) before bootstrap runs
  mkdir -p "$WORKSPACE_DIR"
  chown -R "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$WORKSPACE_DIR" 2>/dev/null || true
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    --profile bootstrap run --rm bootstrap
}

start_primary_tenant() {
  echo "==> Start / reload"
  # Defensive: free the container_name compose is about to claim. `--remove-orphans`
  # only prunes within the current compose project, so a container left behind by
  # a previous deploy under a different project/tenant prefix silently squats on
  # the name and `compose up` fails with `Conflict. The container name is already
  # in use`. We've seen this when BOTJI_TENANT_ID drifted (botji vs botji-single-
  # tenant); the would-be name is whatever ${BOTJI_TENANT_ID:-botji}-hermes
  # resolves to, plus the historical bare `botji-hermes`.
  EXPECTED_CONTAINER="${BOTJI_TENANT_ID:-botji}-hermes"
  for name in botji-hermes "$EXPECTED_CONTAINER"; do
    if docker inspect "$name" >/dev/null 2>&1; then
      echo "    pre-existing container '$name' found — removing to free the name"
      docker rm -f "$name" >/dev/null 2>&1 || true
    fi
  done
  docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    up -d --force-recreate --no-build --remove-orphans
}

record_last_deploy() {
  local status="$1"
  mkdir -p "$DATA_DIR/.botji"
  cat > "$DATA_DIR/.botji/LAST_DEPLOY.json" <<EOF
{
  "deployed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "git_branch": "$GIT_BRANCH",
  "git_commit": "$(git rev-parse HEAD)",
  "image_ref": "$IMAGE_REF",
  "container_health": "$status"
}
EOF
  chown "$HERMES_RUNTIME_UID:$HERMES_RUNTIME_GID" "$DATA_DIR/.botji/LAST_DEPLOY.json" 2>/dev/null || true
}

# ----------------------------------------------------------------------------
# Additional tenants on the same box
# ----------------------------------------------------------------------------
# /opt/botji is the CI-tracked tenant deployed above (it owns the git checkout
# and runs the auth/.env writes from CI secrets). Other tenants (degain, etc.)
# are siblings — same GHCR image, same skills/plugins, but their own .env,
# config.yaml, .codex/auth.json, Telegram bot, and allowlist. We sync code
# components (plugins/skills/schemas) from /opt/botji's checkout into each
# additional tenant's data dir and restart their containers with the new image.
# We do NOT touch their .env, config.yaml, or .codex/auth.json — those are
# tenant-specific and intentionally diverge.
#
# Failures in an additional tenant are logged as warnings but do NOT fail the
# overall deploy: /opt/botji is already healthy at this point and the workflow
# should not roll back a healthy primary because a sibling tenant had trouble.
deploy_additional_tenant() {
  local tenant_path="$1"
  if [ ! -d "$tenant_path" ]; then
    echo "==> Additional tenant $tenant_path: directory missing, skipping"
    return 0
  fi
  if [ ! -f "$tenant_path/.env" ]; then
    echo "==> Additional tenant $tenant_path: .env missing, skipping"
    return 0
  fi

  echo "==> Additional tenant deploy: $tenant_path"

  local tenant_data_dir tenant_id tenant_uid tenant_gid
  tenant_data_dir="$(grep -E '^BOTJI_DATA_DIR=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_id="$(grep -E '^BOTJI_TENANT_ID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_uid="$(grep -E '^HERMES_UID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_gid="$(grep -E '^HERMES_GID=' "$tenant_path/.env" 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
  tenant_data_dir="${tenant_data_dir:-./data/$tenant_id}"
  tenant_uid="${tenant_uid:-10000}"
  tenant_gid="${tenant_gid:-$tenant_uid}"
  local tenant_container="${tenant_id}-hermes"
  local tenant_data_abs="$tenant_path/${tenant_data_dir#./}"

  echo "    tenant=$tenant_id container=$tenant_container data=$tenant_data_abs"

  # Sync plugins / skills / schemas from the primary's checkout into the
  # additional tenant's data dir. Reuses sync_code_components by overriding DATA_DIR
  # + HERMES_RUNTIME_UID/GID, then restoring the primary values.
  local saved_data_dir="$DATA_DIR"
  local saved_uid="$HERMES_RUNTIME_UID"
  local saved_gid="$HERMES_RUNTIME_GID"
  DATA_DIR="$tenant_data_abs"
  HERMES_RUNTIME_UID="$tenant_uid"
  HERMES_RUNTIME_GID="$tenant_gid"
  sync_code_components || echo "    WARNING: additional tenant code sync had non-fatal errors"
  DATA_DIR="$saved_data_dir"
  HERMES_RUNTIME_UID="$saved_uid"
  HERMES_RUNTIME_GID="$saved_gid"
  echo "    code components synced (plugins, skills, schemas)"

  # Best-effort kanban schema migration on the additional tenant's kanban.db.
  migrate_kanban_db_for_tenant "$tenant_data_abs/kanban.db" "$tenant_uid" "$tenant_gid"

  # Remove stale skills metadata files written by a previous (different) image.
  # When a tenant transitions from one hermes-agent image to another, the old
  # image's ~/.hermes/skills/.bundled_manifest is read by the new image's
  # skills_sync.py at startup and can fail with PermissionError or format
  # mismatch — observed 2026-05-22 when degain transitioned from
  # phase0-fidelity-20260520073731 to the GHCR image and crashed in a restart
  # loop. The bundled_manifest is regenerated on every container start, so it
  # is safe to drop. .skills_prompt_snapshot.json is similar.
  for stale in "$tenant_data_abs/skills/.bundled_manifest" "$tenant_data_abs/.skills_prompt_snapshot.json"; do
    if [ -f "$stale" ]; then
      rm -f "$stale"
      echo "    removed stale image-specific metadata: $stale"
    fi
  done

  # Ensure the tenant data tree is traversable by the hermes UID inside the
  # container. Restrictive umasks during prior deploys leave dirs as 0700 —
  # in theory still traversable by the matching owner UID, but PRs #20 and
  # #21 both showed degain crash-looping on PermissionError stat'ing
  # /opt/data/skills/.bundled_manifest until the data root was widened to
  # 755. Chmod the data root itself (non-recursive) and skills/plugins
  # subtrees (-R). go+rX expands directory traversal without granting write,
  # and capital X only sets x where it already exists or on dirs — so
  # secrets (.env at 0600, .codex/auth.json at 0600) stay 0600.
  chmod u+rwX,go+rX "$tenant_data_abs" 2>/dev/null || true
  chmod -R u+rwX,go+rX \
    "$tenant_data_abs/skills" \
    "$tenant_data_abs/plugins" 2>/dev/null || true

  # Recreate the additional tenant container with the new image. The additional tenant's compose
  # file resolves container_name from its own .env (BOTJI_TENANT_ID=degain →
  # degain-hermes), so we just need to be in its directory and pass the image
  # via BOTJI_PROD_IMAGE.
  #
  # CRITICAL: the parent shell exported BOTJI_DATA_DIR / BOTJI_WORKSPACE_DIR
  # from the PRIMARY deploy block (setup_env sets them from /opt/botji's
  # .env). Compose treats shell env as higher priority than the per-tenant
  # .env file, so without an explicit override the additional tenant's
  # compose would silently mount /opt/<tenant>/data/botji/ instead of
  # /opt/<tenant>/data/<tenant>/. Docker auto-creates the missing source dir
  # as root:root, the container sees an empty /opt/data, hermes user can't
  # traverse, and skills_sync.py crashes with PermissionError stat'ing
  # /opt/data/skills/.bundled_manifest. The PR #20 / #21 / #22 chmods were
  # widening permissions on the WRONG (real) dir all along — observed
  # 2026-05-22 when degain restart-looped through four deploys.
  (
    cd "$tenant_path"
    export BOTJI_PROD_IMAGE="$IMAGE_REF"
    export HERMES_UID="$tenant_uid"
    export HERMES_GID="$tenant_gid"
    export BOTJI_TENANT_ID="$tenant_id"
    export BOTJI_DATA_DIR="$tenant_data_dir"
    # BOTJI_WORKSPACE_DIR stays per-tenant (degain has its own ./workspace).
    local tenant_workspace
    tenant_workspace="$(grep -E '^BOTJI_WORKSPACE_DIR=' .env 2>/dev/null | tail -n1 | cut -d= -f2 | tr -d "'\"")"
    export BOTJI_WORKSPACE_DIR="${tenant_workspace:-./workspace}"
    if docker inspect "$tenant_container" >/dev/null 2>&1; then
      docker rm -f "$tenant_container" >/dev/null 2>&1 || true
    fi
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
      up -d --force-recreate --no-build --remove-orphans
  ) || { echo "    WARNING: additional tenant compose up failed — additional tenant left unchanged"; return 0; }

  # Wait for healthy, warn on failure but do NOT fail the primary deploy.
  local tenant_status="not_found"
  for _ in $(seq 1 15); do
    tenant_status="$(docker inspect "$tenant_container" --format='{{.State.Health.Status}}' 2>/dev/null || echo "not_found")"
    [ "$tenant_status" = "healthy" ] && break
    sleep 4
  done
  if [ "$tenant_status" = "healthy" ]; then
    echo "    additional tenant $tenant_container healthy"
    if [ -f scripts/vps-postdeploy-smoke.sh ]; then
      BOTJI_CONTAINER_NAME="$tenant_container" \
        BOTJI_TENANT_ID="$tenant_id" \
        bash scripts/vps-postdeploy-smoke.sh \
        || echo "    WARNING: $tenant_container post-deploy smoke failed (non-fatal — /opt/botji already shipped)"
    fi
  else
    echo "    WARNING: $tenant_container unhealthy ($tenant_status) — /opt/botji deploy already succeeded"
    docker logs "$tenant_container" --tail 30 2>&1 | sed 's/^/      | /' || true
  fi
}

run_additional_tenants() {
  # Run additional-tenant deploys as a non-fatal post-step. List tenants here
  # (whitespace-separated under BOTJI_ADDITIONAL_TENANTS, default = degain).
  BOTJI_ADDITIONAL_TENANTS="${BOTJI_ADDITIONAL_TENANTS:-/opt/botji-degain}"
  for tenant_path in $BOTJI_ADDITIONAL_TENANTS; do
    deploy_additional_tenant "$tenant_path" || true
  done
}
