#!/usr/bin/env bash
# c2pa-gen-dev-cert.sh — generate a self-signed PS256 RSA-2048 cert + key
# for local C2PA signing. Outputs to data/.c2pa/{cert,key}.pem.
#
# Idempotent: refuses to overwrite if either file already exists. Use
# C2PA_REGEN=1 to force regeneration (you should rotate manually instead).
#
# Validity: 365 days. Subject is hard-coded to a Botji dev identity.
# Algorithm: RSA-2048 (the PS256/RSASSA-PSS signature alg is applied at
# sign-time by c2pa-python; the cert just needs to carry an RSA public key).
#
# Platform: bash. Tested on Linux, macOS, and WSL/Git Bash on Windows.
# Windows users: run this from WSL or Git Bash; PowerShell will not execute it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${REPO_ROOT}/data/.c2pa"
CERT_PATH="${CERT_DIR}/cert.pem"
KEY_PATH="${CERT_DIR}/key.pem"

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl not found on PATH" >&2
  exit 1
fi

if [[ -f "${CERT_PATH}" || -f "${KEY_PATH}" ]]; then
  if [[ "${C2PA_REGEN:-0}" != "1" ]]; then
    echo "Cert/key already exist at ${CERT_DIR} — refusing to overwrite." >&2
    echo "Set C2PA_REGEN=1 to force regeneration (rotate manually instead)." >&2
    exit 0
  fi
  echo "C2PA_REGEN=1 set; replacing existing cert + key" >&2
fi

mkdir -p "${CERT_DIR}"
chmod 700 "${CERT_DIR}" 2>/dev/null || true

# Write a temp OpenSSL config so the DN + extensions survive MSYS /
# Git-Bash path translation on Windows (the typical `-subj "/CN=..."` form
# is mangled there). Linux + macOS read the same config without quirks.
# A temp file (rather than a `<(...)` process substitution) avoids the
# `/dev/fd/...` path that openssl on MSYS can't open.
#
# The extensions match what c2pa-rs cert_profile validation requires for
# an end-entity signing cert (C2PA spec §14.5):
#   - keyUsage MUST be present, MUST be critical, MUST include
#     digitalSignature, MUST NOT include keyCertSign
#   - extendedKeyUsage MUST include one of {emailProtection, documentSigning,
#     1.3.6.1.5.5.7.3.36} — we pick emailProtection
#   - basicConstraints CA = false
#   - subjectKeyIdentifier + authorityKeyIdentifier are required by the spec
CONF_FILE="$(mktemp -t botji-c2pa-XXXXXX.cnf)"
trap 'rm -f "${CONF_FILE}"' EXIT

cat >"${CONF_FILE}" <<'OSSL_CONF'
[ req ]
default_bits       = 2048
prompt             = no
distinguished_name = dn
req_extensions     = v3_req
x509_extensions    = v3_ee

[ dn ]
C  = US
O  = Botji
OU = Render
CN = botji-render-dev

[ v3_req ]
basicConstraints       = critical, CA:FALSE
keyUsage               = critical, digitalSignature
extendedKeyUsage       = emailProtection
subjectKeyIdentifier   = hash

[ v3_ee ]
basicConstraints       = critical, CA:FALSE
keyUsage               = critical, digitalSignature
extendedKeyUsage       = emailProtection
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always, issuer
OSSL_CONF

openssl req -x509 -newkey rsa:2048 -sha256 \
  -days 365 -nodes \
  -keyout "${KEY_PATH}" -out "${CERT_PATH}" \
  -config "${CONF_FILE}" \
  -extensions v3_ee >/dev/null 2>&1

chmod 600 "${KEY_PATH}" 2>/dev/null || true
chmod 644 "${CERT_PATH}" 2>/dev/null || true

echo "Generated dev C2PA cert + key:"
echo "  cert: ${CERT_PATH}"
echo "  key:  ${KEY_PATH}"
echo
echo "Export these env vars to use the cert with botji-render:"
echo "  export BOTJI_C2PA_CERT_PATH=\"${CERT_PATH}\""
echo "  export BOTJI_C2PA_KEY_PATH=\"${KEY_PATH}\""
