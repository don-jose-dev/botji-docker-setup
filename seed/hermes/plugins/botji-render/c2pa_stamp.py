"""C2PA + IPTC 2025.1 manifest stamping for Botji render outputs.

Every PNG that leaves ``botji-render`` carries a signed C2PA manifest
declaring it as AI-generated, plus IPTC 2025.1 AI metadata. This is the
contractual surface Botji needs for EU AI Act Article 50 compliance
(Aug 2 2026 deadline).

The single public entry is :func:`stamp_png`. Behaviour:

- Builds a minimum-claim manifest (c2pa.actions.v2 + cawg.training-mining
  opt-out + stds.iptc AI fields + c2pa.ingredients.v2 + RFC 3161 TSA).
- Loads cert + key from ``BOTJI_C2PA_CERT_PATH`` and ``BOTJI_C2PA_KEY_PATH``.
- Signs with PS256 RSASSA-PSS and writes the stamped PNG to ``output_png``.
- Returns a summary dict suitable for embedding in the delivery receipt.

Failures are the caller's problem — this module raises; the caller
(``operations.dispatch``) decides whether to swallow them as a render-soft
warning vs. an outright failure.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Constants per the EU AI Act / IPTC 2025.1 minimum claim set ----------

_DST_TRAINED = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
)
_DST_COMPOSITE = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/"
    "compositeWithTrainedAlgorithmicMedia"
)
_TSA_URL = "http://timestamp.digicert.com"
_SIGNING_ALG = "ps256"
_CLAIM_GENERATOR_PRODUCT = "Botji_Render"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_manifest(
    *,
    botji_version: str,
    source_image_paths: list[Path],
    user_prompt_redacted: str,
    ai_model: str,
    manifest_id: str,
) -> dict:
    """Assemble the C2PA manifest JSON per the EU AI Act minimum claim set."""
    has_sources = len(source_image_paths) > 0
    dst = _DST_COMPOSITE if has_sources else _DST_TRAINED
    claim_generator_info = [{
        "name": "Botji Render",
        "version": botji_version,
        "icon": None,
    }]

    actions_assertion = {
        "label": "c2pa.actions.v2",
        "data": {
            "actions": [{
                "action": "c2pa.created",
                "softwareAgent": {
                    "name": "Botji Render",
                    "version": botji_version,
                },
                "digitalSourceType": dst,
                "parameters": {"ai_model": ai_model},
            }],
        },
    }

    training_mining_assertion = {
        "label": "cawg.training-mining",
        "data": {
            "entries": {
                "cawg.ai_generative_training": {"use": "notAllowed"},
                "cawg.ai_inference": {"use": "notAllowed"},
            },
        },
    }

    iptc_assertion = {
        "label": "stds.iptc",
        "data": {
            "@context": {"Iptc4xmpExt": "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"},
            "Iptc4xmpExt:AIGeneratedContent": True,
            "Iptc4xmpExt:AISystemUsed": ai_model,
            "Iptc4xmpExt:AISystemVersionUsed": ai_model,
            "Iptc4xmpExt:AIPromptInformation": user_prompt_redacted,
        },
    }

    manifest: dict = {
        "claim_generator": f"{_CLAIM_GENERATOR_PRODUCT}/{botji_version}",
        "claim_generator_info": claim_generator_info,
        "title": f"Botji Render Output {manifest_id}",
        "format": "image/png",
        "assertions": [
            actions_assertion,
            training_mining_assertion,
            iptc_assertion,
        ],
    }
    # ingredients.v2 is implicit in the C2PA spec: each ingredient is added
    # via the Builder API; the manifest still needs the assertion shell so
    # readers know the relationship type. We add them via Builder below,
    # so no separate "c2pa.ingredients.v2" assertion is needed here.
    return manifest


def _load_signer_info(cert_path: Path, key_path: Path):
    """Build a c2pa.C2paSignerInfo for PS256 + RFC 3161 timestamping."""
    import c2pa  # local import keeps plugin-load cheap

    return c2pa.C2paSignerInfo(
        alg=b"ps256",
        sign_cert=cert_path.read_bytes(),
        private_key=key_path.read_bytes(),
        ta_url=_TSA_URL.encode("utf-8"),
    )


def stamp_png(
    input_png: Path,
    output_png: Path,
    *,
    botji_version: str,
    source_image_paths: list[Path],
    user_prompt_redacted: str,
    ai_model: str = "gpt-image-2",
) -> dict:
    """Sign ``input_png`` with a Botji C2PA manifest, writing to ``output_png``.

    Returns a manifest summary dict for receipt embedding:
    ``{manifest_id, signed_at_iso, signing_alg, claim_generator}``.

    Raises ``RuntimeError`` on configuration errors (missing cert/key); any
    underlying ``c2pa.C2paError`` propagates so the caller can choose to
    swallow it as a soft warning (see ``operations.dispatch``).
    """
    import c2pa

    cert_env = os.environ.get("BOTJI_C2PA_CERT_PATH")
    key_env = os.environ.get("BOTJI_C2PA_KEY_PATH")
    if not cert_env or not key_env:
        raise RuntimeError(
            "BOTJI_C2PA_CERT_PATH and BOTJI_C2PA_KEY_PATH must be set; "
            "run scripts/c2pa-gen-dev-cert.sh to generate a dev cert"
        )
    cert_path = Path(cert_env)
    key_path = Path(key_env)
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError(
            f"C2PA cert or key not found: cert={cert_path} key={key_path}"
        )
    if not input_png.is_file():
        raise FileNotFoundError(f"input PNG missing: {input_png}")

    manifest_id = f"botji:render:{uuid.uuid4()}"
    signed_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()

    manifest_json = _build_manifest(
        botji_version=botji_version,
        source_image_paths=source_image_paths,
        user_prompt_redacted=user_prompt_redacted,
        ai_model=ai_model,
        manifest_id=manifest_id,
    )

    signer_info = _load_signer_info(cert_path, key_path)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    if output_png.exists():
        output_png.unlink()

    with c2pa.Builder(manifest_json) as builder, \
            c2pa.Signer.from_info(signer_info) as signer:
        # Attach each source image as a c2pa.ingredients.v2 entry. The
        # Builder hashes the bytes and records filename + format.
        for idx, src in enumerate(source_image_paths):
            if not src.is_file():
                logger.warning("c2pa: skipping missing ingredient %s", src)
                continue
            ingredient_json = {
                "title": src.name,
                "relationship": "componentOf",
                "format": _guess_mime(src),
                "label": f"ingredient_{idx}",
            }
            with src.open("rb") as ing_stream:
                builder.add_ingredient(
                    ingredient_json,
                    _guess_mime(src),
                    ing_stream,
                )
        builder.sign_file(str(input_png), str(output_png), signer)

    return {
        "manifest_id": manifest_id,
        "signed_at_iso": signed_at,
        "signing_alg": _SIGNING_ALG,
        "claim_generator": f"{_CLAIM_GENERATOR_PRODUCT}/{botji_version}",
        "tsa_url": _TSA_URL,
        "ingredient_count": len(source_image_paths),
        "output_sha256": _sha256_file(output_png),
    }


def _guess_mime(path: Path) -> str:
    suf = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(suf, "application/octet-stream")


__all__ = ["stamp_png"]
