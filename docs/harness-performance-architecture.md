# Harness performance architecture

> **Status:** proposed implementation contract. Phase A (measurement) must land
> before architectural changes are judged successful. Compatibility-sensitive
> changes remain behind flags until the resource and conformance suites pass.

## Branch implementation status

This branch implements the first compatibility-safe slice:

- Linux procfs whole-tree PSS/RSS/USS, CPU, process, thread, and FD sampling,
  plus fixed/marginal and standalone-versus-Omnigent comparison tools;
- cross-process cold-start admission for Python harness workers, managed Claude
  SDK clients, and managed/native Codex app servers, with queue time excluded
  from the active vendor handshake timeout;
- an opt-in, streaming in-process backend for managed `codex` plus the existing
  `claude-native` and `codex-native` bridge adapters, with explicit per-session
  Codex environments and automatic fallback to the legacy worker;
- payload-free runner/process ownership diagnostics; and
- opt-in child hibernation, strict Claude MCP isolation, and bounded runner
  event queues. Compatibility-sensitive controls remain disabled by default.

It does **not** yet implement shared runner shards, pooled Codex app servers,
the permanent weighted host governor, durable transcript cursors, queue
spill/snapshot recovery, adaptive leases, or default-on migration. The
15-session and 7x3 live Claude/Codex capacity reports must be collected on an
isolated benchmark host before those later phases or the initial numeric gates
can be claimed.

Managed Claude SDK remains isolated. Its current subprocess transport inherits
the parent environment and Omnigent must temporarily remove credential/session
variables during connect. Hosting concurrent Claude SDK adapters in one Python
process would therefore create cross-session environment races. That adapter
stays on the worker path until the SDK supports a fully explicit child
environment.

## Objective

Omnigent should add orchestration, policy, persistence, and cross-harness
behavior without multiplying the cost of the coding harness beneath it. The
meaningful comparison is therefore not “Omnigent versus a harness,” but:

```
Omnigent + harness - harness alone = Omnigent overhead
```

For official shareable adapters, that overhead should approach zero as session
count grows. Native adapters may retain vendor processes when their protocol or
isolation rules require them, but must not add a redundant Python web process
per logical session.

The primary capacity workload is a tree, not a flat collection of idle tabs:
seven attached root sessions, each able to fan out to three subagents (28
logical sessions total). Omnigent must accept the whole tree without causing a
cold-start stampede, starving a root behind its children, or timing out SDK
handshakes while work waits for launch capacity.

## Supported harness boundary

The capacity and compatibility targets in this document apply to Claude Code
and Codex. Neither the runtime nor its benchmark gates depend on another coding
harness.

For native sessions, Claude Code and Codex remain the owners of model context,
tools, permissions, MCP behavior, resume state, and native subagents. Omnigent
supervises their lifecycle and transports events without replacing those
semantics. A Codex-native subagent remains inside Codex's process/session tree;
it is not converted into an Omnigent harness worker.

The density strategy is therefore mechanical rather than behavioral: amortize
Omnigent-owned state, remove redundant wrapper processes, bound queues, and
coordinate cold starts while leaving the vendor harness as the baseline.

## Current Omnigent cost model

The existing zygote in `omnigent/runner/_zygote.py` amortizes Python imports
with copy-on-write forks. That is valuable, but it does not remove private
interpreter, event-loop, thread-pool, queue, client, and mutable transcript
state. Today the common topology is:

```
server
  └─ host daemon
       ├─ runner for root session A
       │    ├─ harness ASGI process for conversation A
       │    │    └─ vendor CLI/server when required
       │    └─ more harness processes for child conversations
       └─ runner for root session B
            └─ ...
```

`HarnessProcessManager` intentionally owns one FastAPI/uvicorn subprocess per
conversation. A one-hour idle window keeps both runner and harness stacks
resident. Runner histories are process-local lists, and runner event and inbox
queues are unbounded. Existing benchmark resource sampling observes only the
server's RSS, so it cannot see most of this topology or distinguish shared from
private pages.

The reported Claude startup failure is a direct consequence of this cost
model. Two managed Claude Agent SDK clients attempted to cold-start while the
machine was heavily oversubscribed. Each connect had a fixed 60-second active
handshake timeout, and the child CLI was allowed to discover non-Omnigent MCP
configuration. A logical subagent could therefore cause unrelated MCP process
and network startup on top of the runner, harness wrapper, and Claude CLI.

## Performance and capacity contract

All byte limits are binary MiB. They are initial gates and may change only when
a committed benchmark report explains why.

### Resource gates

| Property | Initial gate |
|---|---:|
| Marginal Omnigent PSS per active session at N=10, shareable path | <= 15 MiB |
| Omnigent-owned in-process state per hibernated session | <= 1 MiB |
| Official in-process adapter wrapper processes per session | 0 |
| Added warm dispatch p95 | <= 50 ms |
| Unexplained PSS growth after 100 create/turn/delete cycles | <= 5% |
| Unbounded session event/inbox queues | 0 |

### Hierarchical load gate

The reference stress topology is seven attached roots with three children per
root. Tests exercise both a synchronized burst and staggered child creation.

1. All 28 logical sessions are accepted; none fails merely because launch
   capacity is busy.
2. Cold vendor starts pass through a machine-wide, harness-specific admission
   lane. Waiting for a permit is reported as `queued`; it does not consume the
   vendor handshake timeout.
3. Each root has a fair share of launch and active-turn capacity. A single
   root's fan-out cannot occupy every slot while another attached root waits.
4. Attached roots and pending approvals have residency priority. Completed or
   detached children are the first hibernation candidates.
5. A managed Claude SDK child uses the MCP servers explicitly supplied by
   Omnigent and ignores ambient MCP configuration. User/project skill and
   instruction behavior remains controlled separately by the agent spec.
6. The test records queue delay, active connect time, readiness, TTFT, CPU,
   process/thread/FD peaks, and whole-tree PSS. There are no SDK handshake
   timeouts on an otherwise healthy reference host.

“Accepted” does not mean that 28 heavyweight vendor CLIs must execute CPU-heavy
work simultaneously. Admission control keeps the machine useful. Deployments
that require dozens of concurrent model calls should select a direct-provider
runtime; an unavoidable vendor CLI remains part of the harness baseline and
cannot be optimized away by Omnigent.

## Benchmark contract

### Platform and attribution

The authoritative resource gate runs on Linux with procfs. Every sample walks
the complete descendant/process-group set of the declared roots and records:

- PID, parent PID, process group, start-time identity, command, role, root
  session, and logical session where known;
- PSS, RSS, USS (private clean + private dirty), anonymous/file/shared PSS,
  swap, cumulative CPU, thread count, and FD count;
- the roots and discovery rules used, so missing descendants are diagnosable;
- software versions, commit, kernel, CPU count, RAM, allocator settings,
  harness config fingerprint, and benchmark config.

PSS is the primary aggregate because shared pages are divided among their
users. RSS alone must never be used to claim multi-process memory savings.

### Matrix

Each supported harness runs alone and under Omnigent at logical session counts
`0, 1, 2, 5, 10, 15`. The hierarchical 7x3 workload is an additional capacity
case. Each point is repeated at least three times after a separate warmup.

Scenarios:

1. cold process start to protocol-ready;
2. warm idle dispatch and TTFT with a zero-latency provider where possible;
3. active zero-latency turns;
4. post-turn settled memory;
5. large transcript replay;
6. large tool/model output;
7. client disconnect and hibernation;
8. resume from hibernation; and
9. 100 create/turn/delete cycles.

Reports contain raw samples plus median/p95 summaries. For each metric, fit a
least-squares line over session count and report fixed cost (intercept),
marginal cost per session (slope), and paired Omnigent-minus-standalone deltas.
Reject a point when PID attribution is incomplete or samples cross unrelated
host activity thresholds; do not silently average it in.

### Timing boundaries

- **Protocol ready:** child transport has completed its handshake and can
  accept a prompt.
- **Queue delay:** admission request to permit acquisition.
- **Active connect:** permit acquisition to protocol ready. Only this interval
  is governed by the SDK connect timeout.
- **Warm dispatch:** Omnigent receives a turn to the harness receiving it.
- **TTFT:** turn submission to first model-visible text/reasoning delta.
- **Settled:** at least three consecutive resource samples within 2% PSS or a
  documented maximum settle window.

## Target architecture

```
server / durable transcript owner
          │
          ▼
host resource governor ── machine + harness + root-session quotas
          │
          ▼
bounded runner shards keyed by RuntimeKey
          │
          ├─ session actor A ── runtime lease ── harness backend
          ├─ session actor B ── runtime lease ── harness backend
          └─ session actor C ── hibernated cursor only

harness backend:
  in-process adapter | shared vendor daemon | isolated worker/vendor CLI
```

### Runtime identity and isolation

A runner shard may share work only when its `RuntimeKey` matches:

- owner/tenant identity;
- workspace root and per-conversation workdir policy;
- auth identity;
- sandbox and network policy;
- harness name and runtime capability class;
- effective environment/config fingerprint; and
- plugin/community trust class.

No optimization may merge mismatched keys. Community adapters default to an
isolated worker until they explicitly declare and pass the in-process safety
contract.

### Capability-driven harness backends

- **In-process async/ASGI:** safe official SDK adapters; no per-session Python
  web process.
- **Shared multi-session daemon:** vendor protocol supports independent session
  identifiers, cancellation, and cleanup without state leakage.
- **Isolated worker:** adapter is incompatible, blocking, crash-prone, or
  untrusted.
- **Native vendor process:** preserved when unavoidable, but Omnigent adds no
  redundant Python wrapper on the optimized path.

Capabilities, not harness-name conditionals, select the backend. Required
declarations include concurrency safety, session multiplexing, environment
mutability, process isolation, resume support, cleanup semantics, and whether a
vendor process is unavoidable.

Codex app-server exposes multiple independent threads and bounded protocol
queues, but its skill/plugin capability set is still process-home scoped. A
future pool may therefore reuse an app-server only for identical `RuntimeKey`
and capability fingerprints. This branch deliberately keeps one Codex vendor
process per managed conversation while removing the redundant Omnigent worker;
native Codex subagents remain owned by Codex itself.

### Process-free presentation terminals

Top-level SDK sessions advertise the same embedded Omnigent REPL terminal as
before, but registration is process-free. The runner creates the tmux server
and launches `omnigent attach` only when a client first attaches to that
terminal. First attach retains the original cwd, sandbox, transport, sizing,
and lifecycle behavior; later attaches reuse the live pane. Native harness
terminals are unchanged because their vendor TUI is the harness runtime, not an
optional presentation layer.

### Hierarchical resource governor

The host owns admission because per-runner semaphores cannot coordinate a
fan-out across multiple runner processes. The governor maintains:

- machine-wide resident-memory, vendor-start, and active-turn budgets;
- per-harness start/active limits;
- per-root child limits and fair queues;
- reserved root capacity; and
- leases for active turns, attached clients, approvals, background tasks, and
  explicit prewarming.

The first tactical implementation may use a cross-process startup permit for
Claude while the host governor is developed. The permanent implementation must
expose queue state and use weighted fair scheduling rather than a global FIFO.

### Residency and hibernation

A runtime stays resident only while it owns a live lease. On lease expiry:

1. persist the provider resume id, transcript cursor, approvals, and required
   adapter metadata;
2. close the vendor client/process and release derived request payloads;
3. retain only the bounded session actor and durable-store cursor; and
4. reacquire capacity and reconstruct on the next turn/focus event.

The idle delay is adaptive: shorter under memory/CPU pressure and longer when
recent reuse makes a warm runtime valuable. A fixed one-hour default is not the
target behavior.

### Transcript and queue ownership

Durable storage is the canonical transcript. A resident actor keeps a cursor
and small bounded hot window. Replay is paginated and request-local; a
persistent vendor runtime receives deltas. Provider-formatted messages and
large serialized payloads are released after dispatch.

Event and inbox queues are bounded. On overflow the producer advances a
monotonic sequence and emits/requires a snapshot resync, following the existing
bounded server-side session stream pattern. Overflow must not block turn
completion or grow memory without limit.

### Shared immutable and connection state

Compatible sessions share HTTP connection pools, auth/token caches, tool
schemas, MCP connections, model metadata, and immutable prompt/skill content.
The existing runner MCP pool expands across top-level sessions after runner
consolidation. Workspace-specific or mutable MCP servers remain isolated.

### Large-output ownership

Large tool/model results are stored once in the artifact store and represented
in histories/queues by a bounded preview plus an immutable blob reference.
Model-visible truncation is context-aware and reports the original size and
retrieval mechanism. An explicit per-tool/per-agent opt-in can permit a larger
inline result; no path silently drops content.

## Diagnostics

Each runner shard exposes a cheap resource snapshot and can emit a JSONL
timeline. A snapshot includes process-tree PSS attribution, logical-session hot
window/provider/tool/blob/cache bytes, queue depths and drops, live tasks,
runtime leases, subprocesses, allocator signals, and top owners.

After a large transient release or during memory pressure, reclamation is
debounced and threshold-driven. Linux may call allocator trim after GC when the
measured retained delta warrants it. Routine turns must not force full GC.

An incident bundle combines the last timeline window, process tree, capacity
queue, active leases, adapter capabilities, and relevant log tails. A silent
Claude connect timeout should therefore say whether it waited for admission,
how long active connect ran, what child PID existed, and which MCP policy was
applied.

## Rollout

### Phase A — evidence

- Add the procfs sampler, role/session attribution, raw JSON schema,
  slope/delta analysis, and reproducible command manifest.
- Replace server-only RSS in the existing benchmark with whole-tree metrics.
- Add flat `N=0,1,2,5,10,15` and hierarchical 7x3 scenarios.
- Apply managed-child strict MCP isolation and distinguish queue delay from
  active connect time in Claude diagnostics.

### Phase B — reclaim idle state

- Bound runner event/inbox queues with snapshot recovery.
- Move transcript ownership to durable storage and release derived payloads.
- Add leases, hibernation, adaptive idle policy, and large-output references.

### Phase C — consolidate runners

- Add the host resource governor and bounded `RuntimeKey` runner shards.
- Preserve workspace, auth, sandbox, and failure isolation.
- Extend MCP/resource sharing across compatible top-level sessions.

### Phase D — remove wrappers

- Select harness backends from capabilities.
- Move safe official adapters in process.
- Pool vendor daemons only when their protocols prove session isolation.

### Phase E — default and removal

- Run the resource suite and harness conformance suite for every adapter.
- Turn optimized paths on by default after soak testing.
- Remove legacy paths only after rollback telemetry shows no dependency.

## Compatibility and rollback

Flags are additive and independently reversible:

- resource diagnostics and benchmark reporting;
- bounded queues/history ownership;
- leases/hibernation;
- in-process official adapters via
  `OMNIGENT_IN_PROCESS_HARNESSES=codex,claude-native,codex-native`;
- the initial native-only flag
  `OMNIGENT_IN_PROCESS_NATIVE_HARNESSES=claude-native,codex-native` remains an
  accepted compatibility alias;
- ambient Claude MCP isolation via
  `OMNIGENT_CLAUDE_STRICT_MCP_CONFIG=true`;
- active Claude handshake timeout via
  `OMNIGENT_CLAUDE_CONNECT_TIMEOUT_S` (default `60` seconds; queue time excluded);
- Codex app-server cold-start admission via
  `OMNIGENT_CODEX_STARTUP_CONCURRENCY` (default `4`);
- optional bounded event backlogs via
  `OMNIGENT_SESSION_EVENT_QUEUE_MAX_ITEMS` (default `0`, unbounded);
- cache eviction after idle worker reaping via
  `OMNIGENT_HARNESS_CACHE_HIBERNATION=true` (default off);
- shared runner shards/resource governor; and
- capability runtime selection.

A session records the effective flag set and runtime key in diagnostics.
Fallback to an isolated legacy worker is always allowed when capability or
isolation checks fail; falling back must be visible and counted by the
benchmark.

## Non-goals

- Rewriting all of Omnigent in Rust before measuring the Python fixed floor.
- Claiming Omnigent can make an unavoidable vendor CLI cheaper than that CLI's
  standalone baseline.
- Increasing timeouts as the sole response to overload.
- Sharing auth, mutable workspace state, or vendor sessions across incompatible
  isolation keys.
- Trading correctness of resume, fork, steering, policy, approvals, or
  cancellation for density.
