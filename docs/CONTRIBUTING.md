# Contributing — how to change Botji without breaking things

## Rule: seed/ is the source of truth

Never edit files in `data/botji/` directly as your primary change.
Edit in `seed/`, then sync to `data/botji/` and reload.

| What you're changing | Edit here | Sync command |
|---|---|---|
| Agent instructions / tiers | `seed/hermes/SOUL.md` | `cp seed/hermes/SOUL.md data/botji/SOUL.md` |
| Skills | `seed/hermes/skills/<name>/SKILL.md` | `cp seed/... data/botji/skills/.../SKILL.md` |
| Config (models, streaming) | `seed/hermes/config.yaml` | `cp seed/hermes/config.yaml data/botji/config.yaml` |
| Prompt templates (used by botji-artifacts) | `seed/hermes/plugins/botji-artifacts/prompts/` | rebuilt with the plugin — sync via deploy |
| Schemas | `seed/hermes/schemas/` | `cp seed/... data/botji/schemas/...` |
| Plugins | `seed/hermes/plugins/` | rebuild image (`make build`) |

After syncing, always run:
```
make reload
```
This restarts the container and waits until healthy, so the new SOUL.md and config are loaded into a fresh session.

---

## Skill rules (enforced by pre-commit hook)

Every `SKILL.md` must start with YAML frontmatter:

```yaml
---
name: my-skill-name
description: One line — what this skill does and when it triggers.
tags:
  - botji
---
```

The pre-commit hook blocks commits that are missing frontmatter or the `name` field.
Without frontmatter, Hermes's `skill_manage` tool cannot self-improve the skill.

---

## Config changes that require a reload

These settings in `seed/hermes/config.yaml` are only read at startup:

- `streaming.enabled`
- `model.default`
- `auxiliary.*` models and timeouts
- `display.compact`
- `agent.reasoning_effort`
- `plugins.enabled`

After changing any of these: sync to `data/botji/config.yaml` and run `make reload`.

Changes to `SOUL.md` and skill files are also only fully active after a fresh session.

---

## Quick workflow

```bash
# 1. Edit seed files
vim seed/hermes/SOUL.md

# 2. Sync to live
cp seed/hermes/SOUL.md data/botji/SOUL.md

# 3. Commit (hook checks SKILL.md frontmatter)
git add seed/ && git commit -m "improve: ..."

# 4. Reload container
make reload

# 5. Test in Telegram
```

---

## Adding a new skill

```bash
mkdir -p seed/hermes/skills/my-new-skill
cat > seed/hermes/skills/my-new-skill/SKILL.md <<'EOF'
---
name: my-new-skill
description: What this skill does and when to use it.
tags:
  - botji
---

# My New Skill
...
EOF

# Sync to live
cp -R seed/hermes/skills/my-new-skill data/botji/skills/

# Add to bootstrap
# Edit seed/bootstrap-botji.sh: add copy_dir line

# Commit + reload
git add seed/ && git commit -m "add: my-new-skill"
make reload
```
