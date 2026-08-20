---
name: cloud-dispatch
description: Dispatch durable, independent Codex or Claude Omnigent sessions to connected Coder boxes using isolated git worktrees.
---

# Omnigent Cloud Dispatch

Use this skill when the user asks to run coding work in the cloud, on a Coder
box, on another host, or as a multi-box fan-out. In Omnigent, **cloud
dispatch means an independent, top-level Omnigent session on a connected Coder
workspace**. It does not mean `claude --remote`, claude.ai Tasks, or a local
background task. The dispatched session belongs in the main session list, not
under the current chat's sub-agent rail.

This workflow requires `sys_agent_list`, `sys_coder_hosts`, and
`sys_session_create`. If any is unavailable, explain that the current session
cannot perform Coder-backed cloud dispatch. Never silently fall back to local
execution or another remote-dispatch service.

## Required inputs

Resolve these before creating a session:

- One or more concrete coding tasks.
- Provider family: exactly `codex` or `claude`. Refuse `pi` and any other
  provider for this workflow.
- An existing Omnigent agent for that provider.
- An exact git base revision. Accept a branch, remote branch, tag, or commit
  supplied or explicitly confirmed by the user. Never assume `main`.
- The caller repository's `origin` URL when available. Read it without
  modifying git state and send it as `repository_remote` so a similarly named
  checkout cannot be selected. Never run `git remote get-url` without
  redacting credentials first: it expands Git URL rewrites and can print an
  embedded token into the transcript. Use a pipeline that strips HTTPS
  userinfo before it reaches stdout, for example
  `git config --get remote.origin.url | sed -E 's#^(https?://)[^/@]+@#\1#'`.
- Optional Coder box selection. A box may be identified by exact `host_id`,
  exact `coder_workspace_id` / `workspace_id`, or case-insensitive exact
  `host_name` / `workspace_name`.

User invocation authorizes dispatch. Ask only for a missing required input,
an ambiguous agent or box, or a capacity override described below.

## Resolve the agent

1. Call `sys_agent_list` before host discovery.
2. Match the requested provider to these harness families:
   - `codex`: `codex` or `codex-native`
   - `claude`: `claude-sdk` or `claude-native`
3. Prefer a built-in row whose `harness` proves the requested family. For a
   session-bound row, use its `session_id` with `sys_agent_get` to verify the
   harness before using its `agent_id`.
4. Never infer provider family from the agent's display name alone. Never use
   a local config because cross-host placement requires an existing
   `agent_id`.
5. If no accessible agent matches, report that clearly. If multiple match and
   the user did not identify one, show the names, harnesses, and agent IDs and
   ask which to use. Reject a chosen agent whose harness is Pi, unknown, or in
   the other provider family.

The selected harness is the `required_harness` passed to host discovery.

## Discover and select a box

Call `sys_coder_hosts` once per dispatch batch with:

- `required_harness`: the selected agent's exact harness;
- `repository_remote`: the caller origin when available;
- `requested_memory_gib: 4`, unless the user or task supplies a better
  estimate.

Use only the returned `workspace_path`; never use
`coder_reported_directory`.

### Explicitly selected box

- Match only by exact `host_id`, `coder_workspace_id`, or `workspace_id`, or
  by case-insensitive exact `host_name` or `workspace_name`. A partial match
  is not enough.
- Zero matches is a hard failure. Multiple name matches are ambiguous and
  require the user to choose an ID.
- A repository mismatch, unverified workspace path, offline host, missing
  harness, or unauthenticated harness is a hard failure.
- If `override_allowed` is true, show every `ineligibility_reasons` entry and
  ask the user before overriding it. This is limited to unknown or
  insufficient advisory capacity and legacy hosts whose harness readiness is
  unknown. A reported missing or unauthenticated harness is never overridable.
- If creation fails, report the failure. **Never reroute a pinned dispatch to
  another box.**

### Automatic placement

- Consider eligible candidates in the returned rank order.
- For multiple tasks, assign greedily across eligible hosts. After assigning a
  task, subtract its requested memory from that candidate's available-memory
  estimate for the remainder of this batch. Logical CPU count is not a
  session limit.
- When no candidate is eligible but `needs_confirmation` is true, show a
  compact table of candidates with `override_allowed: true`, including their
  capacity and harness-readiness reasons, and ask before proceeding.
- Never override repository, path, connectivity, harness, or authentication
  failures.

## Launch

For each task:

1. Generate a unique branch named `omni/<task-slug>-<short-random-suffix>`.
2. Call `sys_session_create` with `detached: true`, the selected existing
   `agent_id`, `host_id`, verified absolute `workspace_path` as `workspace`,
   generated `branch_name`, exact `base_branch`, concise `title`, and complete
   task as `message`. Never omit `detached: true` for cloud dispatch.
3. Include this safety boundary in the message:

   > Work only inside the isolated worktree created for this session. Do not
   > commit, push, open a pull request, merge, deploy, or alter another
   > worktree unless the user separately authorizes it.

4. For automatic placement only, retry once on the next eligible candidate
   with a fresh branch name after `placement_conflict`,
   `placement_unavailable`, or a host disconnect. Do not retry another host
   when the user selected a box.
5. Report the returned conversation ID, provider and harness, selected box,
   host ID, branch, and worktree. Explain that it is an independent session in
   the main session list and will not report completion into the current chat.
   Monitor later with `sys_session_get_info` or `sys_session_get_history` when
   requested.

## Safety

- Coder metrics are advisory snapshots, not reservations.
- Repository identity and verified paths are security boundaries, not ranking
  hints.
- Never expose Coder tokens or repository credentials in prompts, tool output,
  or results. Sanitize repository URLs before they reach stdout.
- Never commit, push, open a pull request, merge, or deploy without separate
  human authorization.
