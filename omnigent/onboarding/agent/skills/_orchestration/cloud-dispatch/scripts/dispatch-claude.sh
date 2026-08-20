#!/usr/bin/env bash
# Dispatches a single Claude Code cloud session via `claude --remote`.
# Requires the caller's cwd to be a git repo whose current branch matches --branch.

set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: dispatch.sh --branch <base> [--plan] "<task>"

Dispatches one Claude Code cloud session against the current checkout.
The current branch MUST equal <base> — this script does not checkout for you.

Flags:
  --branch <base>   Base branch the session will run against (required).
  --plan            Plan-mode emulation: prepend a "plan only, no edits"
                    instruction to the task prompt. --permission-mode plan
                    is incompatible with --remote (causes local fallback),
                    so plan intent is enforced via the prompt itself.
  -h, --help        Show this help.

EOF
  exit 2
}

branch=""
plan=0
task=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) branch="${2:-}"; shift 2 ;;
    --plan)   plan=1; shift ;;
    -h|--help) usage ;;
    --) shift; task="${1:-}"; shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage ;;
    *)  task="$1"; shift ;;
  esac
done

[[ -n "$branch" ]] || { echo "error: --branch is required" >&2; exit 2; }
[[ -n "$task"   ]] || { echo "error: task string is required" >&2; exit 2; }

command -v claude >/dev/null 2>&1 || { echo "error: claude CLI not found on PATH" >&2; exit 1; }
claude --help 2>&1 | grep -q -- '--remote' || {
  echo "error: your claude CLI does not support --remote (upgrade Claude Code)" >&2
  exit 1
}

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "error: cwd is not a git repo" >&2
  exit 1
}

origin_url=$(git remote get-url origin 2>/dev/null || true)
[[ -n "$origin_url" ]] || { echo "error: no git remote named 'origin'" >&2; exit 1; }
[[ "$origin_url" == *github.com* ]] || {
  echo "error: origin is not a GitHub remote ($origin_url) — --remote only clones from GitHub" >&2
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
PLAN MODE — read-only. Do not edit files, commit, or open a PR. Use only
Read, Grep, Glob, and other read-only tools for inspection.

Your job is to produce a reviewable design document, not a diff. Think at
the level of intent and architecture first; treat specific file changes as
the derived output of that thinking, not the starting point.

Produce a markdown plan with these sections, in order:

1. **Problem & Intent** — what is being asked, why it matters, what the
   successful end state looks like. Call out any ambiguity in the ask.

2. **Key Design Decisions** — the meaningful choices the implementer will
   face, each with a brief rationale and the trade-off being accepted.
   Examples: where a new abstraction lives, sync vs async, new table vs
   column, backwards compatibility stance, config vs convention.

3. **Structural Changes** — new/removed/renamed modules, components,
   endpoints, tables, or data flows. Describe the shape of the system
   after the change, not the edit sequence to get there.

4. **Implementation Approach** — the ordered steps to execute the design,
   grouped into phases if the change is non-trivial. Each step names the
   files/areas touched but stays focused on behavior, not line edits.

5. **Risks & Open Questions** — things that could go wrong, assumptions
   that need validation, decisions deferred to the implementer.

Keep it concise enough to read in one sitting. Favor prose + short bullets
over exhaustive checklists. Output the plan as your final message.

TASK:
$task
EOF
)
fi

args=(--remote "$task")

echo "dispatching: claude --remote <task>  (branch=$branch  plan=$plan)" >&2

# claude --remote falls back to --print mode (rejecting positional prompts) when
# stdin is not a TTY — which is the case under Claude Code's Bash tool, any CI
# runner, or a pipeline. Allocate a PTY via script(1) so claude sees a real TTY.
if [[ -t 0 ]]; then
  exec claude "${args[@]}"
fi

case "$(uname -s)" in
  Darwin)
    # macOS / BSD: script -q <file> <cmd> <args...>
    exec script -q /dev/null claude "${args[@]}"
    ;;
  Linux)
    # GNU script: script -q -c <cmdstring> <file>
    cmd=$(printf '%q ' claude "${args[@]}")
    exec script -q -c "$cmd" /dev/null
    ;;
  *)
    echo "warning: no PTY allocation available for $(uname -s); claude --remote may fail" >&2
    exec claude "${args[@]}"
    ;;
esac
