---
name: botji-source-current
version: 0.1.0
description: Hermes-native current-turn source discipline for Botji. Required before any source-bound render, edit, review, or delivery receipt.
tags: [botji, hermes-native, source, lineage, fidelity]
---

# Botji Source Current

Use this skill before any source-bound transformation.

## Rule

The current user attachment is the source of truth. A source-bound output must
link to source IDs registered for the current turn. Never reuse an older
artifact ID unless the user explicitly says to use the previous file or image.

## Steps

1. Create a concise `current_turn_id` for the active user request.
   Use the session/request ID if Hermes exposes one. Otherwise use a stable
   string made from the current visible turn, for example
   `telegram:<sender_id>:<message_id>` when available.
2. For each current attachment, call:
   `source_register(path=<attachment path>, current_turn_id=<id>, role="source", declared_type=<image|pdf|text|...>)`
3. Before transforming or reviewing, call:
   `source_current(current_turn_id=<id>, required_type=<type>)`
4. Use only returned `source_ids` as parents for this turn.

## Blocks

Block and ask the user to retry or clarify when:

- no current-turn source is registered for a source-bound request,
- a proposed output parent does not match the current-turn source IDs,
- the only available source ID came from an earlier turn,
- the source path is a context-compaction summary instead of a real attachment.

This skill does not judge image quality. It only protects source lineage.
