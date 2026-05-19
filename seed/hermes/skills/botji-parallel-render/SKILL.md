---
name: botji-parallel-render
version: 1.0.0
description: Parallelise multi-image render workloads via delegate_task. Use when the user uploads N source photos and asks for the same operation on each, or asks for several material/finish variants of one source.
tags: [botji, parallel, delegate, multi-image, render, performance]
---

# Botji Parallel Render Skill

Use this skill when one user turn maps to **multiple independent renders**. The default sequential loop is ~50s per `artifact_transform`. A 4-photo "make 3D for all" run takes ~3.5 minutes serial; parallelised it returns in ~50s plus a small overhead. Most botji sessions (≈75%) are multi-image iterative — this is the most leveraged optimisation in the agent.

Per-render contract requirements live in **botji-artifact-fidelity**. The actual sketch-to-render pipeline lives in **botji-2d-to-3d**. This skill only covers *fan-out*.

---

## When to fan out

Use `delegate_task` when:

- User uploads 2–19 source photos and asks for the **same operation on each** ("make 3D for all 4", "render all of these in editorial style").
- User asks for **N variants of one source** ("show me this kitchen in walnut, oak, and white oak").
- Each render is **independent** of the others — no variant needs to see another variant's output.

Do **NOT** fan out when:

- The user is in a **sequential refinement loop** ("now make the wood darker" → "more"). Each turn informs the next; parallelism breaks the conversation.
- Only one render is being produced.
- The user is mid-dialogue (asking a question, clarifying a brief). Subagents cannot talk to the user.
- Total work is under ~2 tool calls per branch — overhead exceeds benefit (see SOUL.md).

---

## Pattern

```
1. Register all sources in the main context (cheap; one artifact_register per file).
2. Spawn one delegate_task per variant — each subagent owns a full render pipeline.
3. Wait for all subagent summaries to return.
4. Collect output_artifact paths + review badges from each summary.
5. Deliver as a single Telegram multi-image batch (v0.12 native media-group send).
```

Register-first is mandatory: subagents inherit a **subset** of the parent context, not the whole conversation. Pass each subagent the explicit `artifact_id` it should operate on — never assume it can see the upload history.

### Tool call sequence

```python
# Step 1 — register every source in the parent (cheap, sequential)
src_ids = [artifact_register(path=p, role="source", declared_type="image")["artifact_id"]
           for p in user_uploaded_paths]

# Step 2 — fan out one task per variant
delegate_task(tasks=[
    {"goal": f"Render 3D photo-fidelity edit of artifact {sid}. "
             f"Follow botji-artifact-fidelity Photo mode (4 steps): "
             f"artifact_transform(operation='edit_image', source_artifact_ids=['{sid}'], ...), "
             f"then artifact_review. Return: output_artifact_id, output path, "
             f"final_claim_level, fidelity_percent, primary_blocker (if any).",
     "toolsets": ["file"], "role": "leaf"}
    for sid in src_ids
])

# Step 3 — collect summaries; build the media group
# Step 4 — single multi-image Telegram send
```

For **N material variants of one source**, the loop is the same but `source_artifact_ids` is identical across tasks and the goal string carries the per-variant material brief.

---

## Failure modes

**One subagent fails, others succeed.** Do NOT block the batch. Deliver the successes as a media group and caveat the failure in the badge:

```
✅ 3 of 4 renders delivered · ❌ photo 2 blocked: [primary_blocker from that subagent's review]
Retry photo 2? (y/n)
```

**Context budget.** Subagents inherit a subset of parent context — they do NOT see the full session history, prior memory loads, or sibling subagent state. Always include the `artifact_id` in the goal string. If the subagent needs user preferences (style, camera, forbidden list), pass them inline in the goal — do not assume `memory` was preloaded.

**Per-render contract.** Each subagent must still honour the full fidelity contract (register → transform → review). Parallelism does not relax the gate. If a subagent skips `artifact_review` to save time, treat its output as `draft`, not `reviewed`.

**Overhead floor.** Spawning a subagent costs ~3–5s. For 1 render, sequential is faster. The break-even is 2 renders; the win compounds at 3+.

---

## Worked example

User sends 3 phone photos of different kitchens with the caption: **"make 3D for all"**.

1. **Parent registers all three sources** (3 cheap calls, ~1s each):
   - `artifact_register(path="/opt/data/uploads/kitchen_a.jpg", role="source", declared_type="image")` → `art_aaa`
   - `artifact_register(path="/opt/data/uploads/kitchen_b.jpg", ...)` → `art_bbb`
   - `artifact_register(path="/opt/data/uploads/kitchen_c.jpg", ...)` → `art_ccc`

2. **Parent fans out three subagents** in a single `delegate_task` call. Each goal string carries one `artifact_id` and instructs the subagent to run Photo mode (artifact_transform `edit_image` + artifact_review) and return the output path + review verdict.

3. **Parent waits.** All three render in parallel (~50–80s wall-clock instead of ~150–240s serial).

4. **Parent collects summaries.** Subagent A: `verified, 94%`. Subagent B: `reviewed, 88%`. Subagent C: `blocked, count conflict`.

5. **Parent delivers a single multi-image Telegram batch** (v0.12 native media-group send) containing the two successful renders, followed by a single badge:

```
✅ 2 of 3 3D renders · route: edit_image · claim: reviewed
❌ photo 3 blocked: element count conflict (sketch had 5 cabinets, render produced 6)
Retry photo 3?
```

The user sees both finished renders within ~80s and one clear caveat — instead of waiting ~4 minutes and getting the failure last.
