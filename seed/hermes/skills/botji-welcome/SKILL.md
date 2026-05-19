---
name: botji-welcome
description: Skill-based welcome flow for cold-start greetings. Replaces a never-built plugin path with an LLM-side rule that calls `clarify` (which renders as native Telegram inline buttons on v0.13+).
tags:
  - botji
  - onboarding
  - ux
  - cold-start
---

# Botji Welcome Skill

Fix the 38 %-of-sessions "user types just 'hi'" cold-start dead-end by routing
greeting-only opening messages through `clarify`, which renders as native
inline-button keyboards on Telegram v0.13+.

## Trigger

Apply this rule when **all** of the following are true on the first turn of a session:

1. The message is from a Telegram chat (or any platform that natively renders
   `clarify` buttons).
2. The user's first message is **greeting-only** — match (case-insensitive,
   ignoring trailing punctuation):
   - `hi`, `hello`, `hey`, `yo`, `gm`, `good morning`, `good evening`,
     `namaste`, `vanakkam`, `hai`
3. There is no other content in the message (no attached photo, no task verb,
   no URL, no question).

If the user includes ANY substantive content with the greeting (e.g. "hi, please
render this kitchen"), skip this skill and route to the normal pipeline.

## Action

Immediately call `clarify` with these four choices, in this order:

```
clarify(
  question = "What would you like me to do?",
  choices = [
    "Make this 3D — send a photo and I'll render it",
    "Material / finish swap on an existing image",
    "Add an element to a photo (wardrobe, light, etc.)",
    "Other — type your request"
  ]
)
```

Do not preface this with a long welcome text. The user wants utility, not warmth.
A single one-line acknowledgement above the choices is fine ("Welcome to Botji.
Pick one:" or similar).

## On user selection

| Choice tapped | Next action |
|---|---|
| "Make this 3D" | Reply: *"Send me a photo of the room / object. Include any dimensions or finish details if you have them."* Wait for the photo. |
| "Material / finish swap" | Reply: *"Send the source photo first. Then tell me the new material in plain words (e.g. 'oak veneer with brass handles')."* |
| "Add an element" | Reply: *"Send the source photo + describe what to add and roughly where (e.g. 'tall wardrobe on the left wall, 60 cm wide')."* |
| "Other" | Reply: *"Go ahead — type what you need. I work best with a photo plus a one-line goal."* |

After the user replies with photo/text, route to the relevant skill:
`botji-2d-to-3d` for render, `botji-artifact-fidelity` for material/element edits,
`botji-prompt-contract` for ambiguous "other".

## Anti-patterns

- Do NOT call `clarify` if the user already provided a task. That's hostile.
- Do NOT use this skill on platforms that render `clarify` as plain text (e.g.
  CLI, API server) — the affordance only works with native button rendering.
- Do NOT loop: if the user types another bare greeting after tapping a button,
  treat it as small-talk and answer normally.
- Do NOT use this for the second-or-later session with the same user. Use
  `session_search` to check; if they've messaged before, skip this flow.

## Why not a plugin

Hermes's plugin extension surface in this repo exposes `pre_gateway_dispatch`,
`transform_llm_output`, and `transform_tool_result`. None of these can emit a new
Telegram message with `InlineKeyboardMarkup` from outside an LLM turn. The
clarify-button rendering is in upstream Hermes core (`nousresearch/hermes-agent`
image), not in plugin extension points exposed here. So the cleanest path is
LLM-side: this skill teaches the agent to fire `clarify` early on greeting-only
opens. Same effective UX, no upstream changes needed.

## Cost note

The cold-start friction today is 38 % of sessions never producing an artifact.
This skill costs one extra LLM turn on the greeting (which already happens — the
bot currently replies with text). Net cost: zero. Net benefit: actual button
affordance instead of "type 1/2/3" guidance.
