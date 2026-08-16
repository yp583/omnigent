import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2Icon,
  CircleDotIcon,
  GitPullRequestIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import {
  listNativePullRequests,
  supportsPullRequestTracking,
  type PullRequestSummary,
} from "@/lib/nativeBridge";
import {
  childSessionsQueryKey,
  fetchChildSessions,
  MAX_TREE_DEPTH,
  type ChildSessionInfo,
} from "@/hooks/useChildSessions";
import { getSessionSlim } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";

const DISMISSED_STORAGE_KEY = "omnigent.personal.dismissed-pull-requests";
const MAX_TREE_SESSIONS = 64;

function dismissalKey(pr: PullRequestSummary): string {
  return `${pr.repository}#${pr.number}:${pr.headSha}`;
}

function readDismissed(): Set<string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(DISMISSED_STORAGE_KEY) ?? "[]") as unknown;
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === "string")
        : [],
    );
  } catch {
    return new Set();
  }
}

async function sessionTreeIds(
  rootSessionId: string,
  queryClient: ReturnType<typeof useQueryClient>,
) {
  const visited = new Set<string>();
  let frontier = [rootSessionId];
  for (let depth = 0; depth <= MAX_TREE_DEPTH && frontier.length > 0; depth += 1) {
    const level = frontier
      .filter((id) => !visited.has(id))
      .slice(0, MAX_TREE_SESSIONS - visited.size);
    level.forEach((id) => visited.add(id));
    if (depth === MAX_TREE_DEPTH || visited.size >= MAX_TREE_SESSIONS) break;
    // Each breadth depends on the child ids discovered in the previous breadth.
    // eslint-disable-next-line no-await-in-loop
    const childLists = await Promise.all(
      level.map((id) =>
        queryClient.fetchQuery<ChildSessionInfo[]>({
          queryKey: childSessionsQueryKey(id),
          queryFn: () => fetchChildSessions(id),
          staleTime: 15_000,
          retry: false,
        }),
      ),
    );
    frontier = childLists.flat().map((child) => child.id);
  }
  return [...visited];
}

function CiStatus({ status }: { status: PullRequestSummary["ciStatus"] }) {
  const config = {
    passing: { label: "CI passing", Icon: CheckCircle2Icon, className: "text-success" },
    pending: { label: "CI pending", Icon: CircleDotIcon, className: "text-muted-foreground" },
    failing: { label: "CI failing", Icon: XCircleIcon, className: "text-destructive" },
    unknown: { label: "CI unknown", Icon: CircleDotIcon, className: "text-muted-foreground" },
  }[status];
  return (
    <span className={cn("inline-flex items-center gap-1", config.className)} title={config.label}>
      <config.Icon aria-hidden="true" className="size-3" />
      <span className="sr-only">{config.label}</span>
    </span>
  );
}

export function PullRequestStrip({
  rootSessionId,
  widthClassName,
}: {
  rootSessionId: string | null;
  widthClassName?: string;
}) {
  const queryClient = useQueryClient();
  const supported = supportsPullRequestTracking();
  const [dismissed, setDismissed] = useState(readDismissed);
  const query = useQuery({
    queryKey: ["personal-pull-requests", rootSessionId],
    enabled: supported && rootSessionId !== null,
    staleTime: 20_000,
    refetchInterval: 30_000,
    retry: false,
    queryFn: async () => {
      const ids = await sessionTreeIds(rootSessionId as string, queryClient);
      const sessions = await Promise.all(
        ids.map((id) =>
          queryClient.fetchQuery({
            queryKey: ["session", id],
            queryFn: () => getSessionSlim(id),
            staleTime: 15_000,
            retry: false,
          }),
        ),
      );
      return listNativePullRequests({
        sessions: sessions.flatMap((session) =>
          session.workspace
            ? [
                {
                  sessionId: session.id,
                  workspace: session.workspace,
                  ...(session.gitBranch ? { branch: session.gitBranch } : {}),
                },
              ]
            : [],
        ),
      });
    },
  });

  const visible = useMemo(
    () => (query.data?.pullRequests ?? []).filter((pr) => !dismissed.has(dismissalKey(pr))),
    [dismissed, query.data?.pullRequests],
  );
  if (!supported || (!query.data?.error && visible.length === 0)) return null;

  const dismiss = (pr: PullRequestSummary) => {
    const next = new Set(dismissed);
    next.add(dismissalKey(pr));
    setDismissed(next);
    try {
      localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify([...next]));
    } catch {
      // The row still dismisses for this page lifetime when storage is blocked.
    }
  };

  return (
    <div
      className={cn("mx-auto mb-2 flex w-full flex-col gap-1.5", widthClassName)}
      data-testid="pull-request-strip"
    >
      {query.data?.error && visible.length === 0 ? (
        <div className="flex items-center gap-2 rounded-xl border bg-card px-3 py-2 text-xs text-muted-foreground">
          <GitPullRequestIcon aria-hidden="true" className="size-3.5 shrink-0" />
          <span>{query.data.error}</span>
        </div>
      ) : null}
      {visible.map((pr) => (
        <div
          key={`${pr.repository}#${pr.number}`}
          className="group flex min-w-0 items-center gap-2 rounded-xl border bg-card px-3 py-2 text-xs shadow-sm"
        >
          <GitPullRequestIcon aria-hidden="true" className="size-3.5 shrink-0 text-brand-accent" />
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            className="min-w-0 flex-1 truncate font-medium hover:underline"
            title={`${pr.repository} · ${pr.title}`}
          >
            #{pr.number} <span className="text-muted-foreground">{pr.branch}</span>
          </a>
          <span className="shrink-0 font-mono text-success">+{pr.additions}</span>
          <span className="shrink-0 font-mono text-destructive">−{pr.deletions}</span>
          <CiStatus status={pr.ciStatus} />
          <button
            type="button"
            aria-label={`Dismiss pull request ${pr.number}`}
            onClick={() => dismiss(pr)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground opacity-60 hover:bg-accent hover:text-foreground group-hover:opacity-100"
          >
            <XIcon aria-hidden="true" className="size-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
