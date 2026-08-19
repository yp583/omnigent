# Coder-backed cloud dispatch

Omnigent can place durable child sessions on connected Coder workspaces
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
- The `coder-dispatch` skill chooses an eligible host and calls the existing
  `sys_session_create` tool with that host, the verified source repository,
  and a unique branch. Omni's existing worktree protocol creates an isolated
  checkout and starts the child runner in it.

Explicit host placement intentionally bypasses normal parent-runner affinity.
An untargeted child still inherits its parent's runner exactly as before.

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

CPU percentage, one-minute load, logical CPU count, and running containers are
ranking signals only. A host can run more coding-agent sessions than logical
CPUs, so no CPU-derived session cap exists. Coder observations are snapshots,
not reservations or runtime guarantees.

The skill automatically uses the highest-ranked eligible host. It asks for a
human decision only when all connected candidates are unmeasured,
over-capacity, or point at the wrong repository. A placement conflict or host
disconnect can be retried on the next eligible candidate.

## Safety boundaries

- Discovery is owner-scoped through both Coder and Omni APIs.
- Coder tokens are kept in process memory and never logged or returned.
- SSH executes one fixed, input-free probe; task text is never interpolated
  into the command.
- Remote sessions are child-only and still pass the existing agent, host,
  workspace, and worktree authorization checks.
- Dispatch does not authorize commits, pushes, pull requests, merges, or
  deployments. Those remain explicit human actions.
