---
name: botji-delivery-receipt
version: 0.1.0
description: Convert Botji skill review results into Hermes-native receipts and call the mechanical delivery gate.
tags: [botji, hermes-native, receipt, gate, delivery]
---

# Botji Delivery Receipt

Use this skill before delivering any reviewed source-bound output.

## Steps

Every source-bound output uses Hermes-native IDs end-to-end (V1R PR 11
retired the legacy `art_*` pipeline):

1. Register the generated output with:
   `output_write(path=<output path>, parents=<current src_* ids>, kind="output", claim_level="reviewed", current_turn_id=<id>, metadata={...})`
2. Convert review checks into a receipt:
   `receipt_record(source_ids=<current src_* ids>, output_id=<out_*>, route=<route label>, status=<pass|warn|block>, checks=<checks>, claim_level="reviewed", current_turn_id=<id>)`
3. Call:
   `delivery_gate(receipt_id=<receipt_id>)`
4. Deliver only when `delivery_gate` is `clear` or `warned`.

Never retry `receipt_record` with the same arguments after an ID-family error.
If it reports a mixed `art_*` + `src_*` payload, the call was holding a stale
legacy ID — re-register the current attachment with `source_register` and use
the new `src_*` ID.

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
