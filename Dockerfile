# syntax=docker/dockerfile:1
ARG HERMES_IMAGE=nousresearch/hermes-agent:main
FROM ${HERMES_IMAGE}

ARG CODEX_NPM_VERSION=latest

# Final USER is intentionally root: the upstream Hermes entrypoint runs as root,
# fixes runtime UID/GID, then drops to the `hermes` user before starting the
# gateway. Setting USER hermes here would break that handoff.
# hadolint ignore=DL3002
USER root

# Install Codex CLI + bundled MCP servers — separate layer so npm cache survives pip changes.
# MCP_FILESYSTEM_VERSION is pinned; bump explicitly when upgrading.
ARG MCP_FILESYSTEM_VERSION=2026.1.14
RUN --mount=type=cache,target=/root/.npm \
    npm install -g \
        "@openai/codex@${CODEX_NPM_VERSION}" \
        "@modelcontextprotocol/server-filesystem@${MCP_FILESYSTEM_VERSION}" \
    && codex --version \
    && mcp-server-filesystem --version 2>/dev/null || true

# Install uv — the 2026 standard Python package manager (10-100x faster than pip,
# single Rust binary, no get-pip.py bootstrap needed).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# System libraries + all Python deps installed into the hermes venv via uv.
# The hermes gateway imports plugins through /opt/hermes/.venv/bin/python,
# so that is the only interpreter that needs these packages.
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update \
      && apt-get install -y --no-install-recommends \
           libmagic1 python3 python3-venv \
           chromium chromium-driver \
      && rm -rf /var/lib/apt/lists/*; \
    fi \
    && ([ -f /opt/hermes/.venv/bin/python ] || python3 -m venv /opt/hermes/.venv) \
    && uv pip install --python /opt/hermes/.venv/bin/python \
         openai \
         pillow \
         pymupdf \
         pypdf \
         ezdxf \
         python-magic \
         jsonschema \
         pydantic \
    && uv pip install --system --break-system-packages \
         ezdxf \
         pillow

COPY runtime/bin/ /usr/local/bin/

RUN chmod +x /usr/local/bin/botji-* \
    && mkdir -p /workspace \
    && chown -R hermes:hermes /workspace \
    && ln -sf /usr/bin/python3 /usr/bin/python

ENV CODEX_HOME=/opt/data/.codex
ENV CHROME_PATH=/usr/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV BOTJI_WORKDIR=/workspace
ENV BOTJI_CONTRACT_SCHEMA=/opt/data/schemas/prompt_contract.schema.json
ENV BOTJI_REVIEW_SCHEMA=/opt/data/schemas/source_fidelity_review.schema.json
ENV BOTJI_ARTIFACT_SCHEMA=/opt/data/schemas/artifact_schema.schema.json
ENV BOTJI_ARTIFACT_ROOT=/opt/data/artifacts
ENV PATH="/opt/data/.local/bin:${PATH}"

# Do not set USER here. The official entrypoint starts as root, fixes runtime UID/GID,
# then drops to the hermes user before starting the gateway.
