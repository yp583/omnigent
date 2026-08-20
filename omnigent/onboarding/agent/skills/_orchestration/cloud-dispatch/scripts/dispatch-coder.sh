#!/usr/bin/env bash
# Dispatches a single durable coding session onto a Coder box (ypbox1/ypbox2)
# over SSH: isolated git worktree off origin/<base> + tmux-wrapped harness run.
# Sessions run unattended with full autonomy (same pattern as ~/codex-automations
# on ypbox1, user-authorized); isolation = dedicated worktree + boundary preamble.

set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: dispatch-coder.sh --box <ypbox1|ypbox2> --harness <claude|codex> --branch <base> [--plan] "<task>"

Dispatches one session onto a Coder box. On the box it fetches origin,
creates an isolated worktree under ~/silico-worktrees/ on a fresh branch off
origin/<base>, and runs the harness non-interactively inside tmux so the
session survives disconnects.

Flags:
  --box <name>       SSH host alias of the box (required; ypbox1 or ypbox2).
  --harness <name>   claude or codex (required).
  --branch <base>    Base branch on origin the worktree starts from (required).
  --plan             Plan-mode emulation: prepend a "plan only, no edits"
                     instruction to the task prompt.
  -h, --help         Show this help.

EOF
  exit 2
}

box=""
harness=""
branch=""
plan=0
task=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --box)     box="${2:-}"; shift 2 ;;
    --harness) harness="${2:-}"; shift 2 ;;
    --branch)  branch="${2:-}"; shift 2 ;;
    --plan)    plan=1; shift ;;
    -h|--help) usage ;;
    --) shift; task="${1:-}"; shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *)  task="$1"; shift ;;
  esac
done

[[ -n "$box"     ]] || { echo "error: --box is required (ypbox1 or ypbox2)" >&2; exit 2; }
[[ -n "$harness" ]] || { echo "error: --harness is required (claude or codex)" >&2; exit 2; }
[[ -n "$branch"  ]] || { echo "error: --branch is required" >&2; exit 2; }
[[ -n "$task"    ]] || { echo "error: task string is required" >&2; exit 2; }
case "$harness" in claude|codex) ;; *) echo "error: --harness must be claude or codex" >&2; exit 2 ;; esac

ssh -o BatchMode=yes -o ConnectTimeout=10 "$box" true 2>/dev/null || {
  echo "error: cannot reach $box over SSH — is the coder port-forward launchd agent running?" >&2
  exit 1
}

if [[ $plan -eq 1 ]]; then
  task=$(cat <<EOF
PLAN MODE — read-only. Do not edit files, commit, or open a PR. Inspect the
repo with read-only means only.

Your job is to produce a reviewable design document, not a diff. Think at
the level of intent and architecture first; treat specific file changes as
the derived output of that thinking, not the starting point.

Produce a markdown plan with these sections, in order: Problem & Intent,
Key Design Decisions, Structural Changes, Implementation Approach, Risks &
Open Questions. Keep it concise enough to read in one sitting. Output the
plan as your final message.

TASK:
$task
EOF
)
fi

task=$(cat <<EOF
Work only inside this worktree. Do not alter other worktrees or the main
checkout. Do not merge or deploy. Committing, pushing, and opening a PR are
allowed only if the task below explicitly asks for them.

$task
EOF
)

slug="dispatch-$(date +%s)-$RANDOM"
task_b64=$(printf %s "$task" | base64 | tr -d '\n')

ssh -o BatchMode=yes "$box" bash -s -- "$slug" "$branch" "$harness" "$task_b64" <<'REMOTE'
set -euo pipefail
slug="$1"; branch="$2"; harness="$3"; task_b64="$4"
repo="$HOME/silico"
wt="$HOME/silico-worktrees/$slug"

git -C "$repo" fetch origin --prune --quiet
git -C "$repo" rev-parse --verify --quiet "origin/$branch" >/dev/null || {
  echo "error: origin/$branch does not exist on the box's checkout" >&2
  exit 1
}

mkdir -p "$HOME/silico-worktrees"
git -C "$repo" worktree add "$wt" -b "$slug" "origin/$branch" --quiet

printf %s "$task_b64" | base64 -d > "$wt/.dispatch-task.txt"

# claude runs the interactive TUI with Remote Control enabled (named by slug,
# visible/steerable from the user's Claude UI) — it needs the tmux pty, so no
# log redirect. codex runs headless exec logged to dispatch.log; box-level
# `codex remote-control` daemon exposes its threads to the ChatGPT app.
case "$harness" in
  codex)  run_line='codex exec --dangerously-bypass-approvals-and-sandbox "$(cat .dispatch-task.txt)" > dispatch.log 2>&1; echo "exit=$?" >> dispatch.log' ;;
  claude) run_line="claude --dangerously-skip-permissions --remote-control $slug \"\$(cat .dispatch-task.txt)\"" ;;
esac

cat > "$wt/.dispatch-run.sh" <<RUNNER
#!/bin/bash
cd "$wt"
$run_line
RUNNER
chmod +x "$wt/.dispatch-run.sh"

tmux new-session -d -s "$slug" "bash $wt/.dispatch-run.sh"

# Each worktree is a new project path, so the claude TUI shows first-run
# dialogs (workspace trust, permissions acceptance) that block unattended
# runs. User-authorized: auto-answer them in whichever order they appear;
# stop once the input prompt ("? for shortcuts") is up. Also mirror the
# pane to dispatch.log.
if [ "$harness" = claude ]; then
  tmux pipe-pane -t "$slug" -o "cat >> $wt/dispatch.log" 2>/dev/null || true
  for _ in $(seq 1 30); do
    sleep 2
    pane=$(tmux capture-pane -pt "$slug" 2>/dev/null || true)
    case "$pane" in
      *"trust this folder"*) tmux send-keys -t "$slug" Enter ;;
      *"Yes, I accept"*)     tmux send-keys -t "$slug" Down; sleep 1; tmux send-keys -t "$slug" Enter ;;
      *"? for shortcuts"*)   break ;;
    esac
  done
fi

echo "box-side OK"
echo "worktree: $wt"
echo "branch:   $slug"
echo "tmux:     $slug"
echo "log:      $wt/dispatch.log"
REMOTE

echo "" >&2
echo "monitor:  ssh $box tail -f '~/silico-worktrees/$slug/dispatch.log'" >&2
echo "attach:   ssh -t $box tmux attach -t $slug" >&2
