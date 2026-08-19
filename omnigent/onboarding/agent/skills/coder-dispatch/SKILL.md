---
name: coder-dispatch
description: Dispatch durable Omnigent coding sessions onto the best connected Coder workspace with isolated git worktrees when sys_coder_hosts is available.
---

# Coder Dispatch

Use this skill when the user asks to run coding work in Coder, in the cloud,
on another machine, on the least-loaded box, or as a multi-machine fan-out.

This workflow requires the Omnigent `sys_coder_hosts` and
`sys_session_create` tools. If either is unavailable, explain that the current
session cannot perform Coder-backed Omni dispatch. Do not fall back to
`claude --remote` or silently run the task locally.

Coder is the resource-information source. Omnigent only identifies its
connected hosts and launches sessions; do not claim Omni collects CPU or
memory telemetry.

## Required inputs

- One or more concrete coding tasks.
- The existing Omnigent `agent_id` to run. If the user supplied an agent name,
  resolve it with `sys_agent_list`.
- The git base revision. Prefer the caller repository's current branch when it
  can be determined. If it cannot be determined safely, ask the user; never
  silently assume `main`.
- The caller repository's `origin` URL when available. Read it without
  modifying git state and pass it as `repository_remote` so a similarly named
  but unrelated Coder checkout cannot be selected.

## Placement

1. Call `sys_coder_hosts` once for the dispatch batch, including the caller's
   `repository_remote` when available. Use `requested_memory_gib: 4` unless the
   task or user gives a better estimate.
2. Consider only candidates with `eligible: true`. Rank is already ordered by
   advisory memory pressure, then CPU/load and container observations.
3. Logical CPU count is not a session limit. A machine can run more coding
   agents than CPUs; CPU count, load, and container count only influence rank.
4. For multiple tasks, spread work greedily across eligible hosts. After each
   assignment, subtract the requested memory from that host's available-memory
   estimate for the rest of this batch. Reuse it when it remains the best
   choice; do not invent a per-CPU session cap.
5. If `needs_confirmation` is true, show a compact candidate table with the
   capacity reasons and ask the human before using an unmeasured or
   over-capacity host. Do not dispatch while that approval is unresolved.

## Launch

For each task:

1. Create a unique branch such as `omni/<task-slug>-<short-random-suffix>`.
2. Call `sys_session_create` with:
   - `agent_id`
   - the selected candidate's `host_id`
   - its verified `workspace_path`
   - the unique `branch_name`
   - the resolved `base_branch`
   - a concise `title`
   - the complete task as `message`, including the selected worktree scope and
     an explicit instruction not to commit, push, open a pull request, merge,
     or deploy without human approval
3. The server creates an isolated worktree on that host before starting the
   session. Never dispatch to `coder_reported_directory`; only use the verified
   `workspace_path` returned by `sys_coder_hosts`.
4. If creation reports `placement_conflict`, `placement_unavailable`, or the
   host disconnects, retry once on the next eligible candidate with a fresh
   branch name.
5. Report each returned session handle, selected host, and branch. Use
   `sys_session_get_info` or `sys_session_get_history` when the user asks for
   progress.

## Safety

- User invocation authorizes session dispatch to an eligible host; do not ask
  for a second confirmation just to use the ranked recommendation.
- Never commit, push, open a pull request, merge, or deploy unless a human
  explicitly requests or approves that action.
- Keep each task inside its generated worktree and task scope.
- Treat all Coder resource values as advisory snapshots, not reservations or
  guarantees.
