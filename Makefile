SHELL := /bin/bash
COMPOSE := docker compose --env-file .env
export HERMES_UID ?= $(shell id -u)
export HERMES_GID ?= $(shell id -g)

.PHONY: init bootstrap build pull up down restart reload logs status shell setup-hermes codex-login codex-status smoke-local smoke-agent snapshot backup-cloud restore clean eval

init:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env"; fi
	@mkdir -p data/botji workspace backups
	@echo "Set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, and API_SERVER_KEY in .env."

bootstrap: init
	$(COMPOSE) --profile bootstrap run --rm bootstrap

build: bootstrap
	$(COMPOSE) build --pull hermes

pull:
	$(COMPOSE) pull || true

up: build
	$(COMPOSE) up -d hermes

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart hermes

reload:
	$(COMPOSE) restart hermes
	@echo "Waiting for healthy..."
	@until docker inspect $${BOTJI_TENANT_ID:-botji}-hermes --format='{{.State.Health.Status}}' | grep -q healthy; do sleep 2; done
	@echo "Ready — fresh session loaded."

logs:
	$(COMPOSE) logs -f hermes

status:
	$(COMPOSE) ps
	$(COMPOSE) exec hermes hermes status || true
	$(COMPOSE) exec hermes codex --version || true

shell:
	$(COMPOSE) exec hermes bash

setup-hermes: bootstrap
	$(COMPOSE) run --rm hermes setup

codex-login: bootstrap
	$(COMPOSE) run --rm hermes codex login

codex-push-auth:
	@[ -n "$(VPS_HOST)" ] || (echo "ERROR: VPS_HOST is required.  make codex-push-auth VPS_HOST=<ip>" && exit 1)
	VPS_HOST="$(VPS_HOST)" \
	VPS_USER="$${VPS_USER:-botji}" \
	VPS_DATA_PATH="$${VPS_DATA_PATH:-/opt/botji/data/botji}" \
	SSH_KEY="$${SSH_KEY:-}" \
	./scripts/codex-push-auth.sh

codex-status:
	$(COMPOSE) run --rm hermes bash -lc 'codex --version && ls -la $$CODEX_HOME && test -f $$CODEX_HOME/config.toml && echo "Codex home present: $$CODEX_HOME"'

smoke-local: bootstrap
	./scripts/smoke-local.sh

smoke-agent:
	./scripts/smoke-agent.sh

snapshot:
	./scripts/snapshot.sh

backup-cloud:
	./scripts/backup-cloud.sh

restore:
	@echo "Usage: make restore BACKUP=backups/<file>.tgz"
	./scripts/restore-snapshot.sh $(BACKUP)

clean:
	$(COMPOSE) down --remove-orphans

eval:
	@python evals/skill_interactions/run_evals.py
