---
name: botji-delivery-receipt
version: 0.1.0
description: Convert Botji skill review results into Hermes-native receipts and call the mechanical delivery gate.
tags: [botji, hermes-native, receipt, gate, delivery]
---

# Botji Delivery Receipt

Use this skill before delivering any reviewed source-bound output.

## Steps

For Hermes-native `src_*` / `out_*` outputs:

1. Register the generated output with:
   `artifact_write(path=<output path>, parents=<current source_ids>, kind="output", claim_level="reviewed", current_turn_id=<id>, metadata={...})`
2. Convert review checks into a receipt:
   `receipt_record(source_ids=<current source_ids>, output_id=<output_id>, route=<route label>, status=<pass|warn|block>, checks=<checks>, claim_level="reviewed", current_turn_id=<id>)`
3. Call:
   `delivery_gate(receipt_id=<receipt_id>)`
4. Deliver only when `delivery_gate` is `clear` or `warned`.

For current production `artifact_*` image outputs:

1. Do not call `artifact_write`. The output is already registered by
   `artifact_transform` as an `art_*` artifact with parents.
2. Convert the `artifact_review` result into:
   `receipt_record(source_ids=<source art_* ids>, output_id=<output art_* id>, route="artifact_transform.edit_image", status=<pass|warn|block>, checks=<checks>, claim_level="reviewed", current_turn_id=<id>)`
3. Call `delivery_gate(receipt_id=<receipt_id>)`.
4. Deliver only when `delivery_gate` is `clear` or `warned`.

Never retry `receipt_record` with the same arguments after an ID-family error.
If it says a `src_*` ID was sent to a legacy artifact tool, switch to the
matching `art_*` ID from `artifact_register` or stop and report the mismatch.

## Status Mapping

- Any missing current source, stale parent, missing output, or semantic blocker:
  `status="block"`
- Source inventory passes but style/premium has caveats:
  `status="warn"`
- Source inventory and quality checks pass:
  `status="pass"`

## User Reply

For `clear`, send the artifact and a short route label.

For `warned`, send the artifact with one concise caveat.

For `blocked`, do not send the artifact as final. State the blocker and ask for
permission to retry or accept a draft.
