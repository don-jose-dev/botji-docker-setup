ARG HERMES_IMAGE=nousresearch/hermes-agent:main
FROM ${HERMES_IMAGE}

ARG CODEX_NPM_VERSION=latest

USER root

# Codex CLI is installed on top of the official Hermes image.
# Keep the official Hermes entrypoint. It owns first-run volume bootstrap and drops privileges.
RUN npm i -g "@openai/codex@${CODEX_NPM_VERSION}" \
    && codex --version

RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update \
      && apt-get install -y --no-install-recommends \
           libmagic1 python3 python3-pip python3-venv \
           chromium chromium-driver \
      && rm -rf /var/lib/apt/lists/*; \
    fi \
    && python3 -m pip install --break-system-packages --no-cache-dir \
      openai \
      pillow \
      pymupdf \
      pypdf \
      ezdxf \
      python-magic \
      jsonschema \
    && /opt/hermes/.venv/bin/python -m ensurepip --upgrade \
    && /opt/hermes/.venv/bin/python -m pip install --no-cache-dir \
      openai \
      pillow \
      pymupdf \
      pypdf \
      ezdxf \
      python-magic \
      jsonschema

COPY runtime/bin/botji-codex /usr/local/bin/botji-codex
COPY runtime/bin/botji-codex-review /usr/local/bin/botji-codex-review
COPY runtime/bin/botji-validate-review /usr/local/bin/botji-validate-review
COPY runtime/bin/botji-contract-new /usr/local/bin/botji-contract-new
COPY runtime/bin/botji-artifact-e2e /usr/local/bin/botji-artifact-e2e
COPY runtime/bin/botji-artifact-harness /usr/local/bin/botji-artifact-harness

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
