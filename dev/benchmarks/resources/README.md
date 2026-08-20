# Harness resource benchmarks

This suite measures memory and process scaling separately from the capability
conformance bench and the latency/throughput journeys.

The gate is Linux-only because `/proc/<pid>/smaps_rollup` provides PSS and USS.
macOS runs may still use the ordinary Omnigent benchmark's server-only psutil
fallback, but those RSS numbers are diagnostic and must not be compared to the
Linux PSS gates.

## Current Omnigent topology

Prepare the locked development environment, then run the flat scaling matrix:

```bash
uv sync --extra all --group dev
uv run --no-sync dev/benchmarks/resources/run_omnigent.py \
  --harness codex \
  --sessions 0,1,2,5,10,15 \
  --runs 3 \
  --settle-seconds 4 \
  --output artifacts/omnigent-resources.json
```

Run the same command again with `--harness claude-sdk`. Only Codex and Claude
Agent SDK are accepted by this driver. Each N point starts a clean server and
host daemon with no extra boot runner, creates exactly N host-backed sessions,
waits for all runners, drives a first turn and warm turn against the
zero-latency mock, then captures settled PSS. The JSON includes raw process
attribution and a least-squares fixed/marginal PSS fit.

To measure the optimized active Codex adapter, add the rollout flag to the same
command:

```bash
OMNIGENT_IN_PROCESS_HARNESSES=codex \
uv run --no-sync dev/benchmarks/resources/run_omnigent.py \
  --harness codex --sessions 0,1,2,5,10,15 --runs 3 \
  --output artifacts/omnigent-codex-in-process.json
```

This removes the per-session Python/uvicorn wrapper, not Codex's own active
app-server. Compare it with an unflagged report using `compare.py`.

## Inspect an already-running tree

Use `capture.py` for a standalone harness or an externally orchestrated run:

```bash
uv run --no-sync -m dev.benchmarks.resources.capture \
  --root harness:session-a=1234 \
  --root harness:session-b=5678 \
  --samples 10 --interval 0.5 \
  --output artifacts/standalone-resources.json
```

Root syntax is `ROLE[:SESSION]=PID`. Descendants and matching process-group
members are attributed to the nearest declared root. A report with missing
roots/processes or missing PSS must not be used for a gate.

The complete architecture, scenario matrix, hierarchical 7x3 fan-out gate,
and rollout rules are in `docs/harness-performance-architecture.md`.

## Standalone comparison

For a harness with a resident server mode, let the suite launch one command per
session (arguments after `--` are never interpreted by a shell):

```bash
uv run --no-sync -m dev.benchmarks.resources.run_command \
  --name vendor --sessions 0,1,2,5,10,15 --runs 3 \
  --output artifacts/vendor-standalone.json -- \
  vendor serve --workspace {workspace}

uv run --no-sync -m dev.benchmarks.resources.compare \
  artifacts/vendor-standalone.json artifacts/omnigent-resources.json \
  --output artifacts/vendor-delta.json
```

The generic driver defines readiness as “still alive after the configured
delay”; its report says so. A harness-specific driver is required before
readiness, dispatch, or TTFT gates can be enforced. The comparison tool uses
only shared N values and reports median paired deltas plus a fixed/marginal
delta fit.
