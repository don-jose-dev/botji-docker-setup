# Security runbook

This document covers the automated security scanning that runs in CI and the
manual steps to take when a scanner flags something.

For first-time deploy setup see [DEPLOYMENT.md](DEPLOYMENT.md); for day-2 ops
see [OPERATIONS.md](OPERATIONS.md).

---

## What runs in CI

| Tool       | What it catches                                                  | Where it runs                 | Blocks PR? |
|------------|------------------------------------------------------------------|-------------------------------|------------|
| gitleaks   | Secrets committed to git (tokens, keys, passwords)               | `.github/workflows/security.yml` | yes        |
| trivy (image) | OS-package and Python CVEs in the built container image        | `.github/workflows/security.yml` | yes (HIGH+CRITICAL only) |
| trivy (fs)    | Dockerfile / compose / Actions misconfigurations               | `.github/workflows/security.yml` | no (informational baseline) |
| dependabot | Outdated GitHub Actions and Docker base images                  | `.github/dependabot.yml` (weekly) | n/a (opens PRs) |
| SBOM       | Software bill of materials for every image built                 | `.github/workflows/deploy.yml`   | n/a (artifact only) |

The security workflow also runs on a weekly schedule so newly-disclosed CVEs
in the base image surface even when nobody touched the repo.

---

## Handling a gitleaks finding

gitleaks fires when it sees something that looks like a real credential in
the diff (or in history, on a full scan).

**If the value is a real secret:**

1. **Rotate it immediately.** The secret is already public the moment it lands
   on a GitHub branch — even if you force-push, assume it has been mirrored.
   - GHCR token → revoke at <https://github.com/settings/tokens>.
   - Telegram bot token → `/revoke` via `@BotFather`, then re-issue.
   - VPS SSH key → `ssh-keygen -y -f` to identify, then remove from
     `~/.ssh/authorized_keys` on the VPS and `Settings → Secrets` in GitHub.
   - OpenAI / Codex auth → revoke at the provider dashboard.
2. Replace the secret in `Settings → Secrets and variables → Actions`.
3. Update the offending commit (rebase + force-push if pre-merge; new commit
   if post-merge — do **not** rewrite shared history without coordination).
4. Note the rotation in the PR description: `credential X rotated in commit
   Y`. Do **not** quote the value.

**If the value is a false positive (a public identifier, an example, etc.):**

1. Add it to `.gitleaks.toml` under `[allowlist]` with a comment explaining
   why it is safe.
2. Re-run the workflow.

The `degain_india_bot` Telegram username is currently allowlisted because it
is a public bot handle, not a token. Bot **tokens** stay in the `VPS_ENV` CI
secret and never touch the repo.

---

## Handling a trivy finding

trivy fires when the built image (or the repo) contains a known CVE at
HIGH or CRITICAL severity.

**Default playbook:**

1. Read the trivy SARIF output (workflow artifact `trivy-image-results`, also
   shown under the repo's `Security → Code scanning` tab).
2. Check whether a fixed version is available upstream. trivy is configured
   with `ignore-unfixed: true`, so any reported CVE has a fix.
3. **Bump the base image or affected package.** Usually this means:
   - Update `ARG HERMES_IMAGE=` (or move from `:main` to a newer pinned tag)
     in `Dockerfile`.
   - Update `MCP_FILESYSTEM_VERSION` or `CODEX_NPM_VERSION` if the CVE is in
     one of those.
4. Re-run the workflow. The CVE should disappear from the report.

**If a CVE genuinely does not apply** (e.g. it is in a code path we never
exercise, or the attack surface is unreachable):

1. Add it to `.trivyignore` with the required comment header (CVE doesn't
   apply, who waived, when to revisit).
2. Open a follow-up issue to revisit on the next base image bump.

Waivers are technical debt. Prefer base image bumps.

---

## Handling a dependabot PR

Dependabot opens one grouped PR per ecosystem per week (Monday 06:00 CET).

1. CI runs the full suite on every dependabot PR — wait for green.
2. Skim the changelog of any major bump. Grouped minor/patch bumps almost
   always merge cleanly.
3. If a PR breaks CI, drop the offending update from the group and re-run.

Dependabot does not currently track `pip` or `npm` because we install both
inside the Dockerfile rather than from a manifest. When a `requirements.txt`
or `package.json` lands, add the matching block to
`.github/dependabot.yml` — the scaffolding is intentional.

---

## SBOM (software bill of materials)

Every image built on `master` produces an SPDX SBOM, attached two ways:

1. As an OCI **referrer** to the image in GHCR (via `sbom: true` in
   `docker/build-push-action`). Auditors can fetch it with
   `docker buildx imagetools inspect <image> --format '{{ json .SBOM }}'`.
2. As a **workflow artifact** (`sbom-<version>` in the run page). 90-day
   retention; sufficient window for incident response.

If a downstream CVE disclosure asks "did the image ship version X of
package Y", grep the SBOM. No need to rebuild the image.

---

## Reporting a vulnerability you discovered

Email <don.jose.dev@gmail.com> with `[security]` in the subject. Do not open
a public issue.

---

## Historical baseline (T4 PR)

The first full-history gitleaks scan against `master` is recorded in PR
`feat: add CI security scanning baseline`. Any findings in that scan are
listed in the PR body (by commit SHA + redacted value) along with the
rotation status. If you are reading this and the security tab is clean, the
baseline was already clean — there is nothing further to do.
