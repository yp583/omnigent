---
name: cloud-dispatch
description: Dispatches one or more cloud coding sessions — Claude Code cloud sessions (claude --remote), Codex Cloud tasks (codex cloud exec), or durable sessions on the Coder boxes (ypbox1/ypbox2 over SSH, isolated worktree + tmux, claude or codex harness) — against a specified base branch, with an optional plan-mode toggle. Use this skill when the user wants to fan out tasks to the cloud, run a task remotely, offload work to a remote session, dispatch onto a coder box, or dispatch parallel cloud agents off a base branch. If the user has not named a vendor, ASK whether they want claude, codex, or coder. Trigger phrases include "fan out", "fanout", "cloud dispatch", "spawn cloud sessions", "run in the cloud", "remote session", "codex cloud", "dispatch to codex", "run on ypbox", "dispatch to the coder box".
---

# cloud-dispatch

Dispatches cloud sessions for either vendor from one workflow:

| Vendor | Backend | Script |
|--------|---------|--------|
| `claude` | `claude --remote` → claude.ai Tasks | `scripts/dispatch-claude.sh` |
| `codex` | `codex cloud exec` → chatgpt.com/codex | `scripts/dispatch-codex.sh` |
| `coder` | SSH to ypbox1/ypbox2 → worktree + tmux, claude or codex harness | `scripts/dispatch-coder.sh` |

All scripts share the same contract: plan mode via prompt prepend, task as
one positional string. claude/codex additionally require cwd to be a git
repo on the base branch with a GitHub origin; coder runs against the box's
own `~/silico` checkout (local cwd/branch don't matter).

UI visibility: the claude harness runs with `--remote-control` (named after
the dispatch slug, steerable at claude.ai/code — the session URL is printed
in the box-side `dispatch.log`). The codex harness is headless-only: the
ChatGPT app does NOT list `codex exec` threads from the boxes, so monitor
codex dispatches via `dispatch.log` / tmux attach; pick the claude harness
when the user wants a UI-steerable session.

## Inputs to gather

Ask the user only for what is missing or ambiguous.

1. **Vendor** — REQUIRED, `claude` or `codex`. **If the user did not name
   one, ask which vendor they want before anything else.** Never default.
2. **Base branch** — REQUIRED. Never default. The current local branch must
   equal it (scripts enforce `HEAD == <base>`).
3. **Task(s)** — REQUIRED. One or more natural-language task descriptions:
   quoted strings, bulleted/numbered lists, or a referenced file (read it and
   extract tasks).
4. **Codex only — environment id** — REQUIRED for codex. Envs are created
   per-repo at chatgpt.com/codex; browse with `codex cloud` (TUI). No local
   default exists — ask if unknown.
5. **Plan mode** — optional, default OFF. Turn ON for "plan", "planning
   only", "don't edit", "dry run", "no code changes". Both scripts implement
   it by prepending a plan-only instruction to the task prompt (for claude,
   `--permission-mode plan` is incompatible with `--remote` and silently
   falls back to local — never use it).
6. **Codex only — attempts** — optional, default 1. `--attempts N` for
   best-of-N.
7. **Coder only — box and harness** — REQUIRED for coder: `--box ypbox1` or
   `ypbox2` (ask if not stated), and `--harness claude` or `codex`.

## Hard rules

- **Always confirm before dispatching.** Print vendor, base branch, plan
  mode, (env id for codex), and the parsed task list, then ask to proceed —
  even for N=1. Cloud sessions cost money and may open PRs.
- **Verify the user is on the base branch locally.** If not, tell them to
  `git checkout <base>` — never auto-checkout, auto-push, or auto-stash. The
  scripts are read-only against local git state except `git fetch`.
- **For N > 1, dispatch in parallel** in a SINGLE Bash call with `&` +
  `wait`, one log file per task. Run exactly ONE `git fetch origin --prune`
  before the fan-out (concurrent fetches race the repo's ref locks).
- **Pass `timeout: 600000` (10 min)** to the Bash call — `claude --remote`
  can take 1–3 min per task to provision.
- Pass multi-line tasks via a file: `"$(cat task.txt)"` — no inline
  multi-line quoting.

## How to invoke

```bash
# claude
~/.claude/skills/cloud-dispatch/scripts/dispatch-claude.sh \
  --branch <base> [--plan] "<task>"

# codex
~/.claude/skills/cloud-dispatch/scripts/dispatch-codex.sh \
  --branch <base> --env <env_id> [--plan] [--attempts N] "<task>"

# coder box
~/.claude/skills/cloud-dispatch/scripts/dispatch-coder.sh \
  --box <ypbox1|ypbox2> --harness <claude|codex> --branch <base> [--plan] "<task>"
```

Fanout (single Bash call, `timeout: 600000`; mixing vendors in one fan-out
is fine — same pattern, per-task script):

```bash
log_dir=/tmp/cloud-dispatch-$(date +%s)
mkdir -p "$log_dir"
D=~/.claude/skills/cloud-dispatch/scripts
"$D/dispatch-claude.sh" --branch <base> "<task 1>" >"$log_dir/1.log" 2>&1 &
"$D/dispatch-claude.sh" --branch <base> "<task 2>" >"$log_dir/2.log" 2>&1 &
wait
ls -la "$log_dir"
```

If even 10 min isn't enough (10+ tasks, slow provisioning), fall back to
fire-and-forget: `nohup <script> ... >/tmp/cloud-dispatch-$$.log 2>&1 & disown`
and read the logs after.

## Confirmation message template

```
About to dispatch <N> cloud session(s):
  vendor:      <claude|codex>
  base branch: <base>
  plan mode:   <on|off>
  env id:      <env_id>          (codex only)
  tasks:
    1. <task 1>
    ...

Prereqs: you must already be on `<base>` locally with origin/<base> up to date.

Proceed?
```

Wait for explicit confirmation. If the user edits the task list, re-show it.

## After dispatch

Report sessions spawned and any non-zero exits with the failing task, then
where to monitor:

- **claude**: https://claude.ai/tasks or `/tasks` in a Claude CLI session.
- **codex**: `codex cloud list` / `codex cloud status <id>`; pull results
  with `codex cloud diff <id>` or `codex cloud apply <id>`, or at
  chatgpt.com/codex.
- **coder**: the script prints monitor/attach commands. claude harness:
  steer from claude.ai/code (URL in `dispatch.log`) or
  `ssh -t <box> tmux attach -t <slug>`; the session stays open for follow-up
  — kill the tmux session and `git worktree remove` when done. codex
  harness: `ssh <box> tail -f <worktree>/dispatch.log`; exits on its own
  (`exit=N` appended to the log).

## Troubleshooting

- **"does not support --remote" / "does not support 'cloud exec'"** → upgrade
  the respective CLI.
- **"current branch is 'X', expected 'Y'"** → `git checkout Y` first.
- **"origin/<branch> does not exist"** → push it first.
- **"origin is not a GitHub remote"** → both backends clone from GitHub only.
- **claude: dispatched but nothing at claude.ai/tasks** → run `/web-setup`
  once to authorize the GitHub app.
- **codex: env id unknown/invalid** → browse the `codex cloud` TUI or create
  an env for the repo at chatgpt.com/codex (one-time per repo).
- **coder: cannot reach box over SSH** → the launchd port-forward agent
  (`com.ypatel.coder-pf-ypbox*`) is down; restart it or `coder port-forward`.
- **coder claude harness: session exits immediately** → a first-run TUI
  dialog wasn't auto-answered; check `dispatch.log` in the worktree for
  which dialog rendered last.
