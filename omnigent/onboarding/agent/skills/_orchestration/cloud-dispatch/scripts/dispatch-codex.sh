#!/usr/bin/env bash
# Dispatches a single Codex Cloud task via `codex cloud exec`.
# Requires the caller's cwd to be a git repo whose current branch matches --branch.

set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: dispatch.sh --branch <base> --env <env_id> [--plan] [--attempts N] "<task>"

Dispatches one Codex Cloud task against the current checkout.
The current branch MUST equal <base> — this script does not checkout for you.

Flags:
  --branch <base>   Base branch the task will run against (required).
  --env <env_id>    Codex Cloud environment id (required; created per-repo at
                    chatgpt.com/codex, browse with \`codex cloud\`).
  --plan            Plan-mode emulation: prepend a "plan only, no edits"
                    instruction to the task prompt.
  --attempts <N>    Best-of-N assistant attempts (default 1).
  -h, --help        Show this help.

EOF
  exit 2
}

branch=""
env_id=""
plan=0
attempts=1
task=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)   branch="${2:-}"; shift 2 ;;
    --env)      env_id="${2:-}"; shift 2 ;;
    --attempts) attempts="${2:-}"; shift 2 ;;
    --plan)     plan=1; shift ;;
    -h|--help)  usage ;;
    --) shift; task="${1:-}"; shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *)  task="$1"; shift ;;
  esac
done

[[ -n "$branch" ]] || { echo "error: --branch is required" >&2; exit 2; }
[[ -n "$env_id" ]] || { echo "error: --env is required (browse envs with 'codex cloud')" >&2; exit 2; }
[[ -n "$task"   ]] || { echo "error: task string is required" >&2; exit 2; }

command -v codex >/dev/null 2>&1 || { echo "error: codex CLI not found on PATH" >&2; exit 1; }
codex cloud exec --help >/dev/null 2>&1 || {
  echo "error: your codex CLI does not support 'cloud exec' (upgrade Codex)" >&2
  exit 1
}

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "error: cwd is not a git repo" >&2
  exit 1
}

origin_url=$(git remote get-url origin 2>/dev/null || true)
[[ -n "$origin_url" ]] || { echo "error: no git remote named 'origin'" >&2; exit 1; }
[[ "$origin_url" == *github.com* ]] || {
  echo "error: origin is not a GitHub remote ($origin_url) — Codex Cloud envs are GitHub-backed" >&2
  exit 1
}

current=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
if [[ "$current" != "$branch" ]]; then
  echo "error: current branch is '$current', expected '$branch'" >&2
  echo "run: git checkout $branch   (and resolve any dirty state) before dispatching" >&2
  exit 1
fi

git fetch origin --quiet
git rev-parse --verify --quiet "origin/$branch" >/dev/null || {
  echo "error: origin/$branch does not exist on GitHub — push it first" >&2
  exit 1
}

if [[ $plan -eq 1 ]]; then
  task=$(cat <<EOF
PLAN MODE — read-only. Do not edit files, commit, or open a PR. Inspect the
repo with read-only means only.

Your job is to produce a reviewable design document, not a diff. Think at
the level of intent and architecture first; treat specific file changes as
the derived output of that thinking, not the starting point.

Produce a markdown plan with these sections, in order:

1. **Problem & Intent** — what is being asked, why it matters, what the
   successful end state looks like. Call out any ambiguity in the ask.

2. **Key Design Decisions** — the meaningful choices the implementer will
   face, each with a brief rationale and the trade-off being accepted.

3. **Structural Changes** — new/removed/renamed modules, components,
   endpoints, tables, or data flows. Describe the shape of the system
   after the change, not the edit sequence to get there.

4. **Implementation Approach** — the ordered steps to execute the design,
   grouped into phases if the change is non-trivial.

5. **Risks & Open Questions** — things that could go wrong, assumptions
   that need validation, decisions deferred to the implementer.

Keep it concise enough to read in one sitting. Output the plan as your
final message.

TASK:
$task
EOF
)
fi

echo "dispatching: codex cloud exec --env $env_id --branch $branch --attempts $attempts <task>  (plan=$plan)" >&2

exec codex cloud exec --env "$env_id" --branch "$branch" --attempts "$attempts" "$task"
