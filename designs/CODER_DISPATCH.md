# Coder-backed cloud dispatch

Omnigent can place durable, independent sessions on connected Coder workspaces
without running its own host resource-telemetry service. Coder remains the
source for dashboard metadata; Omni supplies session authorization, host
launch, and git worktree lifecycle.

## Runtime model

Each candidate Coder workspace runs the normal `omnigent host` process and
advertises its immutable `CODER_WORKSPACE_ID` in the optional `host.hello`
field `coder_workspace_id`. The server persists only that identity. CPU,
memory, load, and container observations are fetched on demand and are never
written to Omni's host table.

An orchestrator with `spawn: true` receives two framework-owned capabilities:

- `sys_coder_hosts` intersects the caller's online Omni hosts with Coder's
  healthy, running, connected workspaces. It reads fresh configured agent
  metadata, queries Coder's agent-container endpoint when needed, and uses a
  bounded fixed Coder SSH probe to verify the git root and fill missing facts.
- The `cloud-dispatch` skill resolves a Codex or Claude agent, chooses an
  eligible host (or honors an explicit box selection), and calls the existing
  `sys_session_create` tool with that host, the verified source repository,
  and a unique branch. Omni's existing worktree protocol creates an isolated
  checkout and starts a detached top-level runner in it.

`coder-dispatch` is a compatibility alias for `cloud-dispatch` and is expected
to be removed in v0.12.0. This workflow does not use Claude's hosted
`claude --remote` service.

Cloud dispatch uses explicit host placement with `detached=true`. The session
therefore appears in the owner's main session list and does not register in the
dispatching chat's child rail or completion inbox. Ordinary untargeted
`sys_session_create` calls remain child sessions and inherit their parent's
runner exactly as before.

## Coder and host setup

The runner executing the orchestrator needs:

- `CODER_URL` for the Coder deployment.
- `CODER_SESSION_TOKEN`, or a non-interactive token available from the local
  `coder login` state.

Each Coder workspace needs:

- the same repository already checked out as its agent's working directory;
- an authenticated Omnigent CLI state for the target server;
- a long-running `omnigent host --background --non-interactive --server
  <omnigent-url>` startup task.
- the selected Codex or Claude harness installed and authenticated under the
  same user and `HOME` as the host service.

Coder normally provides `CODER_WORKSPACE_ID`. A template that removes ambient
Coder variables can set `OMNIGENT_CODER_WORKSPACE_ID` to the workspace UUID
instead. The host mapping survives regenerated Omni host identities and Coder
workspace rebuilds.

Coder agent metadata is template-defined rather than standardized. Discovery
defaults to `mem`, `cpu`, `load`, and `stack_containers` and also checks common
aliases. A metadata value is accepted only when it is nonempty, error-free,
and no older than the greater of the configured maximum age, 30 seconds, or
three reporting intervals.

## Placement semantics

Memory is advisory admission guidance: the default estimate is 4 GiB for the
new coding session plus 1 GiB left free. Missing or insufficient memory makes
a candidate require human override. The fixed SSH probe also verifies that the
working directory is a git root; when the caller supplies its origin remote,
the normalized remote must match.

When `required_harness` is supplied, discovery also checks the host's reported
`configured_harnesses` map. A known missing, outdated, or unauthenticated
harness is a hard failure. Readiness from an older host that did not report the
map is unknown and can be used only after human confirmation.

This readiness is host-global: it can see the host's configured defaults and
CLI login, but not a selected agent's per-spec `executor.auth`. Until placement
becomes agent-aware, a spec-only credential can therefore be conservatively
reported unavailable; configure the provider or login at host scope for cloud
dispatch rather than overriding a known authentication failure.

CPU percentage, one-minute load, logical CPU count, and running containers are
ranking signals only. A host can run more coding-agent sessions than logical
CPUs, so no CPU-derived session cap exists. Coder observations are snapshots,
not reservations or runtime guarantees.

Without an explicit box, the skill uses the highest-ranked eligible host and
asks for a human decision only when the remaining failure is unknown readiness
or advisory capacity. Repository/path mismatches are never overridable. An
automatic placement conflict or disconnect can be retried once on the next
eligible candidate.

An explicit box is matched by exact host/workspace ID or case-insensitive exact
name. A pinned dispatch never silently falls back to another box.

## Safety boundaries

- Discovery is owner-scoped through both Coder and Omni APIs.
- Coder tokens are kept in process memory and never logged or returned.
- SSH executes one fixed, input-free probe; task text is never interpolated
  into the command.
- Detached remote sessions are limited to existing-agent explicit-host
  placement and still pass the existing user, agent, host, workspace, and
  worktree authorization checks.
- The Codex/Claude-only rule is workflow policy in `cloud-dispatch`; the
  underlying discovery and session-create tools remain generic orchestration
  primitives and do not enforce a provider allowlist themselves.
- Dispatch does not authorize commits, pushes, pull requests, merges, or
  deployments. Those remain explicit human actions.
