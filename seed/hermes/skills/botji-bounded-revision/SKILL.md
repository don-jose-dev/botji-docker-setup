---
name: botji-bounded-revision
description: Caps each render job at one revision round per consolidated feedback message — closes the endless-revision-loop pain by making rounds explicit, billable, and reproducible.
tags:
  - botji
  - revision
  - scope
---

# Botji Bounded Revision Skill

Use this skill on every revision request against a prior render. It bounds
the scope of a single revision, marks the round counter on the receipt, and
keeps the conversation from drifting into the endless-feedback pattern that
designer/firm communities describe as the #1 process pain.

## Trigger

Use when:

- The user references a prior render (recipe id, image, or paraphrase such
  as "the kitchen you made earlier").
- The user sends a follow-up message with new constraints, material swaps,
  layout edits, or any change request after a render has been delivered.
- The user asks "can you tweak / change / update / redo X."

Do not use:

- On the first render of a new source (no prior render exists yet).
- On greeting / status / casual messages with no change request.
- On a literal replay request ("send me that render again") — that routes
  to `botji-revision-replay` and does not consume a revision round.

## What counts as one revision round

**One round = all feedback consolidated into a single user turn.** The user
may list many discrete asks inside one message. Each ask is treated as a
preserve/change line on the same round.

The user message that opens a revision round must include:

1. A reference to the prior render (recipe id, image, or unambiguous
   paraphrase the agent can resolve).
2. A change list — one or more discrete asks. Each ask must name what
   changes; "make it better" is not a valid ask.

If the user sends a second message inside the same round before the agent
has delivered the revision, treat the second message as additional
preserve/change lines on the same round — do not open a new round. The
agent must consolidate, restate the merged change list, and ask the user
to confirm before generating.

## What opens a new round

A new round opens only when:

- The prior revision has been delivered (image + receipt sent to the user).
- The user sends a new change request after that delivery.

If the agent has rejected the prior round (gate blocked, source missing,
contract violation), retries against the same source do **not** consume a
new round.

## Procedure

1. Resolve the prior render. Use `botji-source-current` (or the recipe
   store once it ships in PR #5) to pull the parent recipe id, source
   artifact ids, and the change list of the prior round.

2. Build the consolidated change list. Restate every preserve/change ask
   from the current user message. If a previous in-round message exists
   without a delivery, fold its asks in too. Number the asks.

3. Confirm the round with the user. Reply with:

   - "Revision round N for `<recipe_id>` includes:"
   - Numbered change list, each as preserve/change with target region.
   - Estimated provider route (`edit_image` with mask vs full re-render).
   - "Reply OK to proceed, or send any corrections — they will be folded
     into this round."

   Do not start generating until the user confirms.

4. Generate. Route through `botji-router` with the consolidated change list
   as the brief. Mark `revision_round = N` and `parent_recipe_id` on the
   contract.

5. Deliver. The receipt PDF carries:

   - `revision_round` (integer)
   - `parent_recipe_id`
   - `change_list` (numbered)
   - `preserve_list` (numbered)
   - Standard fidelity / claim level fields

6. Close the round in the lineage. The next user message starts at step 1
   of round N+1 only if step 5 completed.

## Hard rules

- **No silent regeneration.** Every revision round produces a fresh
  receipt with `revision_round` set. Reproducing without a receipt is
  forbidden.
- **No round-count inflation.** A failed render that the user did not
  approve does not consume a round. A blocked receipt does not consume
  a round.
- **One round, one image.** Variants (e.g. parallel-render produces 3
  options) all share the same `revision_round` integer. The user picking
  one variant for delivery is not a new round.
- **Free re-render of a round.** If the agent must re-run the same round
  due to provider failure, the receipt records the retry but
  `revision_round` does not advance.
- **Round 0 is the original.** The first accepted render of a source is
  round 0 (the baseline). The first revision is round 1.

## What this skill does not do (V1 scope)

- It does **not** enforce a maximum round count. The substrate does not
  refuse round 4 or round 14. The skill records the counter; firms decide
  their own billable cap in contract.
- It does **not** bill. Receipt carries `revision_round` as metadata only.
  Billing systems consume this externally.
- It does **not** auto-resolve "make it pop more" style asks. If the
  consolidated change list contains a non-specific ask, the contract
  pre-review flags it as missing.

## Receipt fields produced

These fields land on the receipt under existing `botji-delivery-receipt`
(or `botji-client-handoff` after the PR #2 rename pass):

| Field | Value |
|---|---|
| `revision_round` | integer, starts at 0 for baseline |
| `parent_recipe_id` | recipe id this round derives from |
| `change_list` | numbered list of asks the user submitted |
| `preserve_list` | numbered list of asks that constrain what does not change |
| `round_opened_at` | first user message of the round (UTC) |
| `round_delivered_at` | receipt-generation timestamp (UTC) |
| `round_in_flight_seconds` | round_delivered_at - round_opened_at |

`revision_round` and `parent_recipe_id` are required. The rest are best-
effort and may be absent on rounds that did not pass through the receipt
v2 schema (PR #6).

## Reddit / community signal absorbed

This skill is the in-tree absorber for pain cluster #1 of
[`PAIN_MAP.md`](../../../../docs/PAIN_MAP.md): "Scope & revisions —
endless feedback loops, undefined revision rounds, months of back-and-
forth, value-engineering after client fell in love with design." Also
absorbs cluster #7 ("feedback from too many channels") by forcing
consolidation into a single Telegram turn.

## Integrates with

- `botji-prompt-contract` — round counter is part of the contract; not a
  separate substrate.
- `botji-source-current` — to resolve the parent render (today); the
  `botji-recipe` plugin (PR #5 of rewrite) supersedes this lookup with
  recipe-id resolution.
- `botji-router` — routes revisions through this skill before the
  provider route is chosen.
- `botji-delivery-receipt` / `botji-client-handoff` — embeds round
  counter in the receipt.

## Pitfalls

- Do not start a revision round on a render that has no parent. If
  `parent_recipe_id` cannot be resolved, this is a new render, not a
  revision. Route to `botji-router` accordingly.
- Do not silently treat a user reply as same-round when a delivery has
  already been confirmed. The boundary between rounds is the delivered
  receipt, not a time window.
- Do not fold a contract violation ("change to a different room") into
  the round. That is a new source, not a revision. Reset to round 0
  against the new source.

## Cross-references

- [`docs/PAIN_MAP.md`](../../../../docs/PAIN_MAP.md) — pain cluster #1 + #7
- [`docs/SKILL_REWRITE_SEQUENCE.md`](../../../../docs/SKILL_REWRITE_SEQUENCE.md)
  — PR #1 of the rewrite (this skill)
- [`docs/BOTJI_V1.md`](../../../../docs/BOTJI_V1.md) — V1 product contract
- [`botji-prompt-contract/SKILL.md`](../botji-prompt-contract/SKILL.md)
  — the contract this skill mutates
