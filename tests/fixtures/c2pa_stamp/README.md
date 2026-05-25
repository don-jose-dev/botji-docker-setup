# C2PA stamp test fixtures

Real end-to-end tests for the C2PA + IPTC 2025.1 stamping path. No mocks:
they generate real PNGs, sign them against a real PS256 RSA-2048 cert,
fetch a real RFC 3161 timestamp from DigiCert, and validate the manifest
by reading it back.

## Prerequisites

1. Install the runtime deps:

   ```
   uv pip install c2pa-python==0.32.6 Pillow pyyaml
   ```

2. Generate the dev cert + key (one-time, idempotent):

   ```
   bash scripts/c2pa-gen-dev-cert.sh
   ```

   This writes `data/.c2pa/{cert,key}.pem`. Both are gitignored — the
   private key MUST NOT be committed.

3. (Optional) Install `c2patool` for the gold-standard CLI validation.
   The prebuilt binary at <https://github.com/contentauth/c2patool/releases>
   is built against an older c2pa-rs and may emit `claim could not be
   converted from CBOR` for newer SDK output — the tests fall back to
   the in-process `c2pa.Reader` validator in that case (still real, just
   not the CLI tool).

## Running

```
# end-to-end stamp + reader validation
.venv/Scripts/python tests/fixtures/c2pa_stamp/test_c2pa_stamp.py

# dispatch-level integration (enabled / disabled / soft-fail paths)
.venv/Scripts/python tests/fixtures/c2pa_stamp/test_dispatch_integration.py
```

Both scripts exit 0 on success and print human-readable assertions.

## What the tests prove

- `stamp_png` produces a real PNG with a C2PA JUMBF chunk.
- The manifest contains all five EU AI Act Article 50 elements:
  `c2pa.actions.v2` with `c2pa.created`, `stds.iptc` with
  `AIGeneratedContent=true`, `cawg.training-mining` opt-out, ingredients
  attached via the Builder API, and an RFC 3161 timestamp.
- The signature validates (`claimSignature.validated`) and the
  timestamp validates (`timeStamp.validated`) when read back.
- `dispatch()` stamps PNG outputs by default, skips non-PNG outputs,
  honours `BOTJI_C2PA_ENABLED=0`, and treats stamp errors as soft
  failures that don't break the render.
