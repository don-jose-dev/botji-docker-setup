# Hermes Gap Matrix for Botji V1R

Audit of `C:\Dev\hermes-agent` source to inform the V1R rewrite plan.
Every "Hermes has" claim cites a file:line ref into that tree. Botji-side
claims cite `seed/hermes/plugins/` and `evals/` in this repo.

## delegate_task

- **Hermes has:** Defined in `tools/delegate_tool.py:1918` as
  `delegate_task(goal, context, toolsets, tasks, max_iterations, acp_command,
  acp_args, role, parent_agent)`. Two modes: single (`goal`) or batch
  (`tasks` JSON array).
  - **Parallelism default:** `_DEFAULT_MAX_CONCURRENT_CHILDREN = 3`
    (`tools/delegate_tool.py:132`), tunable via
    `delegation.max_concurrent_children` or env
    `DELEGATION_MAX_CONCURRENT_CHILDREN`
    (`tools/delegate_tool.py:329`). Hard reject at submission if
    `len(tasks) > max_children` (`tools/delegate_tool.py:2008-2016`).
    Single task runs inline; batch runs through a `ThreadPoolExecutor`
    (`tools/delegate_tool.py:2101`).
  - **Child context:** Fresh, not inherited.
    `tools/delegate_tool.py:10` ("A fresh conversation (no parent
    history)") and `_build_child_agent` (`tools/delegate_tool.py:870`)
    constructs a new `AIAgent` from a focused system prompt
    (`_build_child_system_prompt`, `tools/delegate_tool.py:971`) built
    only from `goal + context`. Each child gets its own `child_task_id`
    derived from a stable `sa-<index>-<uuid>` subagent id
    (`tools/delegate_tool.py:1482`). Toolsets are inherited from the
    parent's enabled set (or supplied list, intersected with the
    parent's), then `_strip_blocked_tools` removes `delegate_task`,
    `clarify`, `memory`, `send_message`, `execute_code`
    (`tools/delegate_tool.py:45-53`, `tools/delegate_tool.py:945-961`).
  - **Result-gather semantics:** Parent blocks until all children
    finish. Single-task path returns the result dict directly
    (`tools/delegate_tool.py:2091-2095`). Batch path polls
    `concurrent.futures.wait(..., timeout=0.5)` so the parent can bail
    on interrupt (`tools/delegate_tool.py:2122-2165`). Results are
    sorted by `task_index` so output order matches input
    (`tools/delegate_tool.py:2213`). Each entry: `{task_index, status,
    summary, error, api_calls, duration_seconds}` (plus internal
    `_child_role`, `_child_cost_usd` stripped before return).
  - **Error/timeout handling:** Per-child hard timeout via a nested
    `ThreadPoolExecutor` whose `future.result(timeout=child_timeout)`
    enforces `_get_child_timeout()` (default
    `DEFAULT_CHILD_TIMEOUT = 600` s, `tools/delegate_tool.py:513`,
    tunable via `delegation.child_timeout_seconds`,
    `tools/delegate_tool.py:367`). On timeout: child interrupted, entry
    written with `status="timeout"`, 0-API-call timeouts get a worker
    stack-dump diagnostic (`tools/delegate_tool.py:1544-1558`). On
    exception: entry has `status="error"`. Hooks are wrapped per-callback
    (`hermes_cli/plugins.py:1318-1329`), so a failing child never breaks
    sibling collection.
  - **Other guards:** Depth cap `MAX_DEPTH = 1`
    (`tools/delegate_tool.py:133`, clamped to `[_MIN_SPAWN_DEPTH=1,
    _MAX_SPAWN_DEPTH_CAP=3]` at `tools/delegate_tool.py:420`). Children
    are leaf by default; `role="orchestrator"` re-grants the delegation
    toolset (`tools/delegate_tool.py:967-968`). Heartbeats from a daemon
    thread propagate child activity to the parent every 30 s and stop
    after stale thresholds so a wedged child can't keep the parent
    "active" forever (`tools/delegate_tool.py:1369-1437`).
- **Hermes gap:** None for the V1R scope. The primitive is mature,
  documented, configurable, and emits `subagent_stop` hooks
  (`tools/delegate_tool.py:2268`) that Botji could observe.
- **Botji currently does:** Nothing. `grep -r 'delegate_task' seed/` and
  `seed/hermes/skills/` returns no matches — Botji has not built any
  parallel/fan-out machinery and does not call `delegate_task` from
  skills, hooks, or substrate code.
- **Action:** Use Hermes primitive directly when V1R needs fan-out
  (parallel render attempts, batch fixture replays, etc.). Nothing to
  shelve.

## Kanban workflow dispatch

- **Hermes has:** `workflow_template_id` exists as a nullable column on
  `tasks` (`hermes_cli/kanban_db.py:844`), is exposed on the dataclass
  (`hermes_cli/kanban_db.py:637`), is writable via the kernel
  (`hermes_cli/kanban_db.py:1190-1192` adds the column on migration),
  and is filterable via `list_tasks(..., workflow_template_id=...)`
  (`hermes_cli/kanban_db.py:1664, 1683-1685`). The dashboard plugin API
  surfaces it as a query filter
  (`plugins/kanban/dashboard/plugin_api.py:357,380`) and the CLI does
  the same (`hermes_cli/kanban.py:397,1376`). **No runtime dispatch
  reads this field.** The kernel never consults `workflow_template_id`
  to choose a worker, advance a step, or route a task. The official
  upstream docstring states this explicitly:
  `website/docs/user-guide/features/kanban.md:800` — "Two nullable
  columns on `tasks` are reserved for v2 workflow routing:
  `workflow_template_id` (which template this task belongs to) and
  `current_step_key` (which step in that template is active). The v1
  kernel ignores them for routing but lets clients write them, so a v2
  release can add the routing machinery without another schema
  migration." Test
  `tests/hermes_cli/test_kanban_core_functionality.py:1736-1753`
  asserts the columns are writable; no test exercises a dispatch path.
- **Hermes gap:** No workflow-template registry, no dispatch loop, no
  step-state machine. v2 routing is named in docs but not implemented
  in this snapshot.
- **Botji currently does:** Ships
  `seed/hermes/kanban/workflows/render_retry.yaml` (PR #37) — a
  7-step template encoding the canonical render-retry shape — plus
  skill references that point agents at it. The skill-following agent
  reads the template; no Botji substrate code dispatches from it.
  Confirmed by the commit message of `0f423e6` (PR #37): "the shape
  here is a forward-looking client contract that the skill-following
  agent reads now, and that v2 routing will consume later."
- **Action:** Keep as a forward-compat scaffold for the skill-driven
  read path; understand that **no runtime gain** comes from the
  `workflow_template_id` field today. PR #37 is not a strip candidate
  (the YAML is read by skills), but any V1R plan that assumes Hermes
  will route off the field is premature. Watch upstream for the v2
  routing kernel; do not invest in mirroring it in Botji code.

## transform_llm_output hook

- **Hook contract:**
  - **Registered name:** `transform_llm_output` in `VALID_HOOKS`
    (`hermes_cli/plugins.py:136`).
  - **Kwargs delivered:** `response_text, session_id, model, platform`
    (`agent/conversation_loop.py:4039-4045`).
  - **Return contract:** Plugins return a string to replace the
    response, or `None`/empty to leave unchanged. "First non-None
    string wins" (`hermes_cli/plugins.py:134-135` and
    `agent/conversation_loop.py:4035`). Implementation:
    `agent/conversation_loop.py:4046-4049` iterates results and
    `break`s on the first truthy string.
- **Fail-open behavior:** Yes, two layers.
  1. `PluginManager.invoke_hook` wraps every callback in
     `try/except`; an exception just logs a warning and the next
     callback runs (`hermes_cli/plugins.py:1318-1329`).
  2. The conversation_loop call site itself is wrapped in
     `try/except` and logs `transform_llm_output hook failed`
     (`agent/conversation_loop.py:4050-4051`), so the original
     `final_response` ships unchanged if anything goes sideways.
- **Ordering across plugins:** Callbacks run in the order they were
  registered (`hermes_cli/plugins.py:1316` reads
  `self._hooks.get(hook_name, [])` which is a list appended to during
  plugin discovery). **First non-None string wins** — later plugins
  cannot see or chain off an earlier plugin's transformation. This is
  not a pipeline; it is a single-winner pattern.
- **Lifecycle position:** Fired in
  `agent/conversation_loop.py:4036-4051`, AFTER the tool-calling loop
  completes and AFTER the file-mutation-verifier footer is appended
  (`agent/conversation_loop.py:4022-4030`), but BEFORE `post_llm_call`
  (`agent/conversation_loop.py:4053-4070`). Only fires when
  `final_response and not interrupted` — empty or user-interrupted
  turns skip the hook.
- **Capability boundary:** It can **rewrite** the user-facing text
  (string replacement, redaction, full overwrite to an error message).
  It cannot **hard-block delivery** in the sense of aborting the turn
  or preventing the response from being sent — returning `None` ships
  the original; returning a string ships that string. The hook receives
  no tool-call history, no message list, and no per-turn metadata
  beyond `session_id/model/platform`, so it cannot inspect the
  reasoning trail or the prior tool outputs.
- **Hermes gap:** No hard-block primitive at this seam. To prevent a
  response from being sent, a plugin must replace the text with an
  error message (which is what `botji-core/hooks/delivery_check.py`
  does).
- **Botji currently does:**
  `seed/hermes/plugins/botji-core/hooks/delivery_check.py` (PR #38)
  registers `transform_llm_output` to enforce delivery rules. On
  failure it replaces the reply with `❌ Internal: source-bound output
  missing receipt. Not delivering.` Hook is explicitly fail-open
  (`delivery_check.py:88-90`). Detection is conservative: only fires
  when text contains BOTH a delivery marker (`route:`, `claim:`,
  `verdict:`, `delivery_gate:`) AND a literal `art_*`/`out_*` ID
  (`delivery_check.py:34, 40-46`).
- **Action:** Use Hermes primitive as-is; Botji's hook implementation
  is the correct shape. Note in V1R docs that "hard block" is a
  string-replacement convention, not a runtime abort — and that
  ordering is registration-order-first-wins, so only one plugin should
  own the transform hook for any given decision dimension to avoid
  silent override.

## ImageGenProvider

- **Hermes has:** Abstract base in `agent/image_gen_provider.py:51`:

  ```
  class ImageGenProvider(abc.ABC):
      @property @abc.abstractmethod
      def name(self) -> str: ...

      @abc.abstractmethod
      def generate(self, prompt: str,
                   aspect_ratio: str = DEFAULT_ASPECT_RATIO,
                   **kwargs: Any) -> Dict[str, Any]: ...
  ```

  (`agent/image_gen_provider.py:130-143`). Default optional methods:
  `is_available()`, `list_models()`, `get_setup_schema()`,
  `default_model()`. Response shape documented at
  `agent/image_gen_provider.py:13-26`: `{success, image, model, prompt,
  aspect_ratio, provider}` with `image` being a URL or saved-file path.
- **Source-bound editing support:** **No.** The `generate` signature
  takes `prompt: str` and `aspect_ratio` only; `**kwargs` is
  forward-compat scaffolding ("implementations should ignore unknown
  keys", `agent/image_gen_provider.py:140-143`). No
  `source_image`, `input_image`, `reference_image`, or
  `preserve_regions` parameter exists. Confirmed by greps across
  `C:\Dev\hermes-agent` for `source_image|preserve_region` (no
  matches) and inspection of the four shipped providers:
  - `plugins/image_gen/openai/__init__.py:173-227` (OpenAI direct,
    pure `prompt + size + quality + n`)
  - `plugins/image_gen/openai-codex/__init__.py:269-359` (Codex OAuth,
    pure `prompt + size + quality`)
  - `plugins/image_gen/xai/__init__.py:160` (xAI)
  - `plugins/image_gen/fal/__init__.py:100` (fal.ai)

  None of the four accept image inputs. The interface is
  generation-from-prompt only.
- **Hermes gap:** The plugin interface cannot model edit/inpaint
  workflows. There is no contract slot for source images, reference
  weights, or preserve-region masks.
- **Botji currently does:** Bypasses `ImageGenProvider` entirely.
  `seed/hermes/plugins/botji-artifacts/_codex.py:21-79` constructs
  the Codex Responses API call directly with `input_image` content
  blocks for each source artifact (lines 50-55). The Botji prompt
  layering (fidelity_baseline + image_generation template) and
  `provider_route` selection (`_codex.py`) are all custom Botji logic
  living outside the Hermes provider abstraction.
- **Action:** **Keep — gap is fundamental.** The Hermes interface
  cannot host Botji's source-bound editing without an upstream change
  to `ImageGenProvider.generate`'s signature (add `source_images:
  list[ImageInput] | None`, `preserve_regions: ... | None`, etc.).
  V1R PR 12 cannot ride on the existing primitive; either (a) upstream
  the interface extension, or (b) keep Botji's direct-API path as a
  long-term plugin-private implementation. Recommend (a) only if a
  second provider would also benefit; otherwise (b) is the smaller
  risk surface.

## Skill progressive disclosure

- **Hermes has:** Two-tier disclosure. Metadata (name + description) is
  always injected into the system prompt; full body loads on demand
  via the `skill_view` tool.
  - **Metadata-in-system-prompt loader:**
    `agent/prompt_builder.py:997` defines
    `build_skills_system_prompt(available_tools, available_toolsets)`,
    which produces a compact index keyed by category. It is called from
    `agent/system_prompt.py:178-181` and the result is appended to
    `stable_parts` when the agent has the skills tools enabled
    (`agent/system_prompt.py:169-185`). This runs once per system-prompt
    rebuild (cached at two layers: in-process LRU
    `_SKILLS_PROMPT_CACHE`, `agent/prompt_builder.py:1039-1043`, and a
    disk snapshot `.skills_prompt_snapshot.json`,
    `agent/prompt_builder.py:1046`). So **metadata is always-on at
    turn start** for any agent with the `skills` toolset.
  - **What is in metadata:** Per-skill `name` (≤64 chars) and
    `description` (≤1024 chars), grouped by category, with optional
    category descriptions. Limits at
    `tools/skills_tool.py:94-95` (`MAX_NAME_LENGTH = 64`,
    `MAX_DESCRIPTION_LENGTH = 1024`). The doc string at
    `tools/skills_tool.py:9-12` calls out the tiers explicitly:
    "Metadata (name ≤64 chars, description ≤1024 chars) - shown in
    skills_list. Full Instructions - loaded via skill_view when needed.
    Linked Files (references, templates) - loaded on demand."
  - **Full-body loader:** `tools/skills_tool.py:850` defines
    `skill_view(skill_name, file_path=None, ...)` which reads the
    skill's `SKILL.md` (or a linked file under
    `references/`/`templates/`/`assets/`) and returns full content
    plus parsed frontmatter. `skills_list`
    (`tools/skills_tool.py:675-740`) is a tool too, but the body of
    a skill never enters the prompt unless `skill_view` is invoked
    (or the skill is part of a "bundle" served via
    `_serve_plugin_skill`, `tools/skills_tool.py:746-848`, which also
    only returns content on explicit request).
  - **What is in body:** Full SKILL.md content (frontmatter + prose +
    internal references), optional linked files served on a second
    explicit `skill_view` call. Body is NEVER injected into the
    system prompt; the agent must elect to load it.
- **Hermes gap:** None for V1R skill needs. The two-tier shape is
  exactly what the V1R rewrite plan assumes. The only caveat is that
  metadata loading is platform/tool-set sensitive: the index filters by
  `available_tools`, `available_toolsets`, platform, and disabled-skill
  list (`agent/prompt_builder.py:1024-1037`), so a skill whose
  `prerequisites`/`conditions` don't match the current agent
  configuration simply won't appear in the metadata roster.
- **Botji currently does:** Authors skills under
  `seed/hermes/skills/botji-*/SKILL.md` (e.g.
  `botji-prompt-contract`, `botji-render-mode`, `botji-fidelity-rules`)
  and relies on Hermes' loader without any Botji-side override. No
  Botji code reimplements the metadata-vs-body split.
- **Action:** Use Hermes primitive directly. Audit V1R skills against
  the 64/1024-char limits and the `available_tools`/`platforms`
  filters so they don't silently disappear from the metadata roster.

## Codex text surface

- **Hermes has:** Yes, a text-completion path against Codex OAuth is
  reachable from plugins, but it is explicit-opt-in, not auto-routed.
  - **Provider routing:** `agent/auxiliary_client.py:1871` defines
    `_build_codex_client(model)` which constructs an OpenAI SDK client
    against the Codex backend
    (`_CODEX_AUX_BASE_URL =
    "https://chatgpt.com/backend-api/codex"`,
    `agent/auxiliary_client.py:441`) using the Codex OAuth token from
    `~/.hermes/auth.json` (`_read_codex_access_token`,
    `agent/auxiliary_client.py:1351`). Cloudflare-friendly headers
    (`originator: codex_cli_rs`, `ChatGPT-Account-ID` from JWT) are
    attached via `_codex_cloudflare_headers`
    (`agent/auxiliary_client.py:444-480`).
  - **Plugin surface:** `agent/plugin_llm.py` exposes
    `ctx.llm.complete(messages, ...)` and
    `complete_structured(instructions=..., input=..., json_schema=...)`
    (`agent/plugin_llm.py:17-26`) for trusted plugins. Override knobs
    (`provider`, `model`, `agent_id`, `profile`) are gated behind
    per-plugin config in `config.yaml` (`agent/plugin_llm.py:36-53`).
    A plugin with `llm.allow_provider_override: true` and
    `allowed_providers: [openai-codex]` (or `["*"]`) can request
    `provider="openai-codex", model="<explicit-codex-model>"` and the
    auxiliary router will resolve via `_build_codex_client`.
  - **Important constraints:**
    - **Not in the auto fallback chain.** The auxiliary client
      docstring at `agent/auxiliary_client.py:25-30` is explicit:
      "Codex OAuth (ChatGPT-account auth) is intentionally NOT in
      either fallback chain ... Codex is used only when the user's
      main provider IS openai-codex (Step 1 above) or when a caller
      explicitly requests it with a model
      (auxiliary.<task>.provider + auxiliary.<task>.model)."
    - **Caller must supply the model.** No hardcoded default —
      `agent/auxiliary_client.py:1873-1887`: "There is no
      auto-selection of the Codex model: the ChatGPT-account Codex
      endpoint's accepted model list is an undocumented, drifting
      allow-list, so any hardcoded default we pick goes stale."
    - **Cloudflare/IP gating.** From `agent/auxiliary_client.py:447-451`:
      the Codex endpoint serves 403s to non-residential IPs unless
      the headers are right. Container-hosted (VPS) callers can hit
      this. The header workaround is in place but the failure mode is
      worth flagging in any V1R deployment doc.
- **Hermes gap:** None as such — the surface exists. The friction is
  operational (Cloudflare gating from VPS-class IPs) rather than
  missing-primitive.
- **Botji currently does:** `evals/skill_interactions/judge.py` (PR
  #35) uses `from openai import OpenAI; client = OpenAI()`
  (`judge.py:127-132`) directly against `client.responses.create`
  (`judge.py:148`). It does not route through Hermes' auxiliary client
  or `plugin_llm.complete_structured`; it assumes a raw
  `OPENAI_API_KEY` is present and uses model `gpt-5.4-mini`
  (`judge.py:35`). Botji's `seed/hermes/plugins/botji-artifacts/_codex.py`
  already knows how to build a Codex OAuth client for image generation
  (`_codex.py:31`), so the patterns exist side-by-side in this repo.
- **Action:** **Rewrite the judge to use Hermes' Codex text surface.**
  Specifically: move `evals/skill_interactions/judge.py` from raw
  `openai.OpenAI()` to either (a) `ctx.llm.complete_structured(...)`
  inside a small plugin gated with
  `allow_provider_override: true, allowed_providers: [openai-codex]`,
  or (b) direct use of `agent.auxiliary_client.call_llm(..., provider=
  "openai-codex", model=<codex model>)` if running outside the plugin
  surface. Either path reuses the OAuth token in
  `~/.hermes/auth.json` and the Cloudflare header workaround, removing
  the OpenAI-API-key dependency that contradicts the tenant deployment
  model. Do NOT strip the judge — the text surface exists, so the
  judge can be redeployed on it.

---

## Summary of gate decisions

| Question | Decision |
|---|---|
| Q2: Does `workflow_template_id` drive runtime dispatch? | No. Forward-compat columns only; v1 kernel ignores them for routing. Botji PR #37 is a skill-readable client contract, not a dispatcher — keep, but do not assume runtime gain. |
| Q4: Can `ImageGenProvider` host source-bound editing? | No. Interface is generation-from-prompt only; no source/preserve-region slots. V1R PR 12 cannot ride on the existing primitive — keep Botji's direct Codex Responses path or upstream an interface extension. |
| Q6: Is a Codex text surface usable from a plugin? | Yes. `auxiliary_client._build_codex_client` + `plugin_llm.complete_structured` give a plugin Codex text access (explicit opt-in, model must be supplied, not in auto-fallback). PR #35's judge should be rewritten onto this surface rather than stripped. |
