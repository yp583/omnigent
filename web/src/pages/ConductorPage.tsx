import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import {
  ArrowUpRightIcon,
  BotIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  FileTextIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  RadioIcon,
  SaveIcon,
  SendIcon,
  TriangleAlertIcon,
} from "lucide-react";

import { PageScroll } from "@/components/PageScroll";
import { ConductorVoicePanel } from "@/components/ConductorVoicePanel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  bindConductor,
  getConductorDashboard,
  listConductorMemory,
  readConductorMemory,
  updateConductorMemoryProvider,
  writeConductorMemory,
  type ConductorMemoryDocument,
  type ConductorSession,
} from "@/lib/conductorApi";
import {
  listNativePullRequests,
  supportsPullRequestTracking,
  type PullRequestSummary,
} from "@/lib/nativeBridge";
import { relativeTime } from "@/lib/relativeTime";
import { Link, useSearchParams } from "@/lib/routing";
import { postEvent } from "@/lib/sessionsApi";
import { cn } from "@/lib/utils";

const DASHBOARD_KEY = ["conductor", "dashboard"] as const;
const MEMORY_KEY = ["conductor", "memory"] as const;

export function ConductorPage() {
  const [searchParams] = useSearchParams();
  const dashboard = useQuery({
    queryKey: DASHBOARD_KEY,
    queryFn: getConductorDashboard,
    refetchInterval: 5_000,
  });

  if (dashboard.isLoading) return <PageState label="Loading Conductor…" />;
  if (dashboard.isError || !dashboard.data) {
    return (
      <PageState
        label="Conductor is unavailable"
        detail={dashboard.error instanceof Error ? dashboard.error.message : undefined}
        retry={() => void dashboard.refetch()}
      />
    );
  }
  if (!dashboard.data.conductor) {
    return <ConductorSetup sessions={dashboard.data.sessions} />;
  }
  if (searchParams.get("view") === "overview") {
    return <ConductorWorkspace dashboard={dashboard.data} />;
  }
  // Conductor is a persistent chat agent, not a dashboard the user has to
  // operate. Once its transcript is bound, the top-level nav opens that chat
  // directly; the session itself can inspect the permission-bounded ledger.
  return <Navigate to={`/conductor/${dashboard.data.conductor.conversationId}`} replace />;
}

function ConductorSetup({ sessions }: { sessions: ConductorSession[] }) {
  const queryClient = useQueryClient();
  const candidates = useMemo(
    () => sessions.filter((session) => session.conductorEligible),
    [sessions],
  );
  const [bindingId, setBindingId] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (conversationId: string) => bindConductor(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY }),
  });

  return (
    <PageScroll maxWidthClassName="max-w-5xl" contentClassName="px-5 md:px-8">
      <div className="max-w-3xl pt-[clamp(1rem,4vw,3.5rem)]">
        <p className="mb-4 text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
          Cross-session control
        </p>
        <h1 className="max-w-2xl text-[clamp(2rem,5vw,3.75rem)] leading-[0.98] font-semibold tracking-[-0.045em]">
          Give one session the wider view.
        </h1>
        <p className="mt-6 max-w-xl text-base leading-7 text-muted-foreground">
          Your Conductor gets a dedicated transcript and memory, watches sessions you own or that
          teammates share with you, and routes you to work that needs a decision. Existing work
          chats are never reused.
        </p>
      </div>

      <div className="mt-12 border-t">
        <div className="flex items-center justify-between gap-4 py-4">
          <div>
            <h2 className="text-sm font-semibold">Start its dedicated chat</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Only sessions created with the built-in Conductor agent are eligible.
            </p>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to="/?agent=conductor&conductorSetup=1">Start Conductor chat</Link>
          </Button>
        </div>
        {candidates.length === 0 ? (
          <div className="border-y py-10 text-sm text-muted-foreground">
            No dedicated Conductor chat exists yet. Starting one will bind it automatically before
            its first message is sent.
          </div>
        ) : (
          <div className="divide-y border-y">
            {candidates.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => setBindingId(session.id)}
                className={cn(
                  "group flex w-full items-center gap-4 px-1 py-4 text-left transition-colors hover:bg-muted/40",
                  bindingId === session.id && "bg-muted/60",
                )}
              >
                <span
                  className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded-full border",
                    bindingId === session.id && "border-foreground bg-foreground text-background",
                  )}
                >
                  {bindingId === session.id ? (
                    <CheckCircle2Icon className="size-4" />
                  ) : (
                    <BotIcon className="size-4 text-muted-foreground" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {session.title || "Untitled session"}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {session.workspace || "No workspace"} · {relativeTime(session.updatedAt * 1000)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
        <div className="flex min-h-20 items-center justify-between gap-4">
          <span role="alert" className="text-xs text-destructive">
            {mutation.error instanceof Error ? mutation.error.message : null}
          </span>
          <Button
            disabled={!bindingId || mutation.isPending}
            onClick={() => bindingId && mutation.mutate(bindingId)}
          >
            {mutation.isPending && <Loader2Icon className="size-4 animate-spin" />}
            Make Conductor
          </Button>
        </div>
      </div>
    </PageScroll>
  );
}

function ConductorWorkspace({
  dashboard,
}: {
  dashboard: Awaited<ReturnType<typeof getConductorDashboard>>;
}) {
  const running = dashboard.sessions.filter((session) => session.status === "running").length;
  const waiting = dashboard.sessions.reduce(
    (count, session) => count + session.pendingApprovalCount,
    0,
  );
  const [ledgerScope, setLedgerScope] = useState<"personal" | "shared" | "all">("personal");
  const personalSessions = dashboard.sessions.filter(
    (session) => session.accessScope === "personal",
  );
  const sharedSessions = dashboard.sessions.filter((session) => session.accessScope === "shared");
  const visibleSessions =
    ledgerScope === "all"
      ? dashboard.sessions
      : ledgerScope === "shared"
        ? sharedSessions
        : personalSessions;
  const queryClient = useQueryClient();
  const providerChange = useMutation({
    mutationFn: updateConductorMemoryProvider,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY });
      void queryClient.invalidateQueries({ queryKey: MEMORY_KEY });
    },
  });

  return (
    <PageScroll
      maxWidthClassName="max-w-7xl"
      contentClassName="px-4 sm:px-6 lg:px-10"
      data-testid="conductor-page"
    >
      <header className="flex flex-col gap-6 border-b pb-7 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <RadioIcon className="size-3.5" />
            {running > 0 ? `${running} working now` : "All quiet"}
            {waiting > 0 && <span>· {waiting} waiting for you</span>}
          </div>
          <h1 className="text-3xl font-semibold tracking-[-0.035em]">Conductor</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            One operational view across your sessions and chats teammates explicitly shared with
            you.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
          <span className="sr-only" id="memory-provider-label">
            Memory provider
          </span>
          <Select
            value={dashboard.conductor!.memoryProvider}
            onValueChange={(value) => providerChange.mutate(value)}
            disabled={providerChange.isPending || dashboard.memoryProviders.length < 2}
          >
            <SelectTrigger size="sm" aria-labelledby="memory-provider-label">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {dashboard.memoryProviders.map((provider) => (
                <SelectItem key={provider} value={provider}>
                  {provider} memory
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <ConductorVoicePanel
            conversationId={dashboard.conductor!.conversationId}
            config={dashboard.conductor!.config}
            onConfigUpdated={() => void queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY })}
          />
          <Button asChild variant="outline">
            <Link to={`/c/${dashboard.conductor!.conversationId}`}>
              Open transcript <ArrowUpRightIcon className="size-4" />
            </Link>
          </Button>
        </div>
      </header>

      <ConductorPullRequests sessions={personalSessions} />

      <div className="grid gap-10 pt-8 lg:grid-cols-[minmax(0,1.55fr)_minmax(19rem,0.85fr)]">
        <section aria-labelledby="active-work-heading">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2
              id="active-work-heading"
              className="text-xs font-semibold tracking-[0.12em] uppercase"
            >
              Session ledger
            </h2>
            <div className="flex items-center gap-1" aria-label="Session scope">
              {(
                [
                  ["personal", `Mine ${personalSessions.length}`],
                  ["shared", `Shared ${sharedSessions.length}`],
                  ["all", `All ${dashboard.sessions.length}`],
                ] as const
              ).map(([scope, label]) => (
                <Button
                  key={scope}
                  type="button"
                  size="sm"
                  variant={ledgerScope === scope ? "secondary" : "ghost"}
                  aria-pressed={ledgerScope === scope}
                  onClick={() => setLedgerScope(scope)}
                >
                  {label}
                </Button>
              ))}
            </div>
          </div>
          <div className="divide-y border-y">
            {visibleSessions.length === 0 ? (
              <div className="py-12">
                <p className="text-sm font-medium">
                  {ledgerScope === "shared" ? "No chats shared with you." : "No sessions here yet."}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {ledgerScope === "shared"
                    ? "A teammate’s chat appears here after they share it directly with you."
                    : "New work will appear here as soon as you start it."}
                </p>
              </div>
            ) : (
              visibleSessions.map((session) => (
                <SessionLedgerRow key={session.id} session={session} />
              ))
            )}
          </div>
        </section>
        <MemoryDesk />
      </div>
    </PageScroll>
  );
}

function SessionLedgerRow({ session }: { session: ConductorSession }) {
  const [steering, setSteering] = useState(false);
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState(false);
  const mutation = useMutation({
    mutationFn: async () =>
      postEvent(session.id, {
        type: "message",
        data: { role: "user", content: [{ type: "input_text", text: message.trim() }] },
      }),
    onSuccess: () => {
      setMessage("");
      setSent(true);
      window.setTimeout(() => setSent(false), 2_000);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (message.trim() && !mutation.isPending) mutation.mutate();
  }

  const state = session.pendingApprovalCount
    ? "waiting"
    : session.status === "running"
      ? "running"
      : session.status === "failed"
        ? "failed"
        : "idle";

  return (
    <article className="group py-4" data-status={state}>
      <div className="flex min-w-0 items-start gap-3">
        <SessionPulse state={state} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="min-w-0 truncate text-sm font-medium">
              {session.taskSummary || session.title || "Untitled session"}
            </h3>
            {session.pendingApprovalCount > 0 && (
              <span className="rounded-full bg-brand-accent/12 px-2 py-0.5 text-[11px] font-medium text-brand-accent">
                Needs response
              </span>
            )}
            {session.accessScope === "shared" && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                {session.canSteer ? "Shared · can steer" : "Shared · read only"}
              </span>
            )}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {session.gitBranch && (
              <span className="inline-flex min-w-0 items-center gap-1">
                <GitBranchIcon className="size-3 shrink-0" />
                <span className="truncate">{session.gitBranch}</span>
              </span>
            )}
            <span>{relativeTime(session.updatedAt * 1000)}</span>
            {session.accessScope === "shared" && session.ownerUserId && (
              <span className="truncate">Shared by {session.ownerUserId}</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {session.canSteer && (
            <Button variant="ghost" size="sm" onClick={() => setSteering((value) => !value)}>
              <MessageSquareTextIcon className="size-3.5" />
              Steer
            </Button>
          )}
          <Button asChild variant={session.pendingApprovalCount ? "outline" : "ghost"} size="sm">
            <Link to={`/c/${session.id}`}>{session.pendingApprovalCount ? "Review" : "Open"}</Link>
          </Button>
        </div>
      </div>
      <div
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-200 ease-out",
          steering ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        )}
      >
        <form onSubmit={submit} className="min-h-0 overflow-hidden">
          <div className="mt-3 ml-7 flex items-end gap-2">
            <Textarea
              rows={2}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder={`Steer ${session.title || "this session"}…`}
              aria-label={`Message ${session.title || "session"}`}
              className="min-h-16 resize-none"
            />
            <Button
              type="submit"
              size="icon"
              disabled={!message.trim() || mutation.isPending}
              aria-label="Send steering message"
            >
              {mutation.isPending ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : sent ? (
                <CheckCircle2Icon className="size-4" />
              ) : (
                <SendIcon className="size-4" />
              )}
            </Button>
          </div>
          {mutation.isError && (
            <p role="alert" className="mt-1 ml-7 text-xs text-destructive">
              {mutation.error instanceof Error ? mutation.error.message : "Message failed"}
            </p>
          )}
        </form>
      </div>
    </article>
  );
}

function SessionPulse({ state }: { state: "waiting" | "running" | "failed" | "idle" }) {
  const config = {
    waiting: { label: "Waiting for approval", className: "bg-brand-accent" },
    running: { label: "Running", className: "bg-success animate-pulse" },
    failed: { label: "Failed", className: "bg-destructive" },
    idle: { label: "Idle", className: "bg-muted-foreground/35" },
  }[state];
  return (
    <span className="flex h-6 w-4 shrink-0 items-center justify-center" title={config.label}>
      <span aria-hidden className={cn("size-2 rounded-full", config.className)} />
      <span className="sr-only">{config.label}</span>
    </span>
  );
}

function MemoryDesk() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: MEMORY_KEY, queryFn: listConductorMemory });
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const document = useQuery({
    queryKey: [...MEMORY_KEY, selectedPath],
    queryFn: () => readConductorMemory(selectedPath as string),
    enabled: selectedPath !== null,
  });
  const [draft, setDraft] = useState("");

  useEffect(() => {
    if (!selectedPath && list.data?.length) {
      setSelectedPath(
        list.data.find((item) => item.path === "MEMORY.md")?.path ?? list.data[0].path,
      );
    }
  }, [list.data, selectedPath]);
  useEffect(() => {
    if (document.data) setDraft(document.data.content ?? "");
  }, [document.data]);

  const save = useMutation({
    mutationFn: () =>
      writeConductorMemory({
        path: selectedPath as string,
        content: draft,
        expectedRevision: document.data?.revision ?? 0,
      }),
    onSuccess: (next) => {
      queryClient.setQueryData([...MEMORY_KEY, selectedPath], next);
      void queryClient.invalidateQueries({ queryKey: MEMORY_KEY });
    },
  });
  const dirty = document.data !== undefined && draft !== (document.data.content ?? "");

  return (
    <aside aria-labelledby="memory-heading" className="min-w-0 lg:border-l lg:pl-8">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="memory-heading" className="text-xs font-semibold tracking-[0.12em] uppercase">
          Memory desk
        </h2>
        <span className="text-xs text-muted-foreground">Markdown</span>
      </div>
      <div className="border-y">
        <div className="flex gap-1 overflow-x-auto border-b py-2 [scrollbar-width:none]">
          {(list.data ?? []).map((item) => (
            <button
              key={item.path}
              type="button"
              onClick={() => setSelectedPath(item.path)}
              className={cn(
                "shrink-0 rounded-md px-2 py-1 text-[11px] transition-colors",
                selectedPath === item.path
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {shortMemoryPath(item)}
            </button>
          ))}
        </div>
        {document.isLoading || list.isLoading ? (
          <div className="flex min-h-64 items-center justify-center text-xs text-muted-foreground">
            <Loader2Icon className="mr-2 size-3.5 animate-spin" /> Loading memory…
          </div>
        ) : document.isError || list.isError ? (
          <div role="alert" className="min-h-64 py-8 text-xs text-destructive">
            Memory could not be loaded.
          </div>
        ) : selectedPath ? (
          <>
            <div className="flex items-center gap-2 px-1 py-2 text-[11px] text-muted-foreground">
              <FileTextIcon className="size-3" />
              <span className="min-w-0 flex-1 truncate">{selectedPath}</span>
              <span>r{document.data?.revision ?? 0}</span>
            </div>
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label={`Edit ${selectedPath}`}
              className="min-h-64 resize-y rounded-none border-x-0 font-mono text-xs leading-5 shadow-none focus-visible:ring-0"
            />
            <div className="flex min-h-12 items-center justify-between gap-3 px-1">
              <span role="alert" className="text-[11px] text-destructive">
                {save.error instanceof Error ? save.error.message : null}
              </span>
              <Button
                size="sm"
                variant={dirty ? "default" : "ghost"}
                disabled={!dirty || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <SaveIcon className="size-3.5" />
                )}
                Save
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </aside>
  );
}

function shortMemoryPath(document: ConductorMemoryDocument) {
  if (document.path === "MEMORY.md") return "Memory";
  return document.path.replace(/\.md$/, "").split("/").at(-1) || document.path;
}

function ConductorPullRequests({ sessions }: { sessions: ConductorSession[] }) {
  const supported = supportsPullRequestTracking();
  const targets = useMemo(
    () =>
      sessions.flatMap((session) =>
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
    [sessions],
  );
  const query = useQuery({
    queryKey: ["conductor", "pull-requests", targets],
    queryFn: () => listNativePullRequests({ sessions: targets }),
    enabled: supported && targets.length > 0,
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
  const pullRequests = query.data?.pullRequests ?? [];
  if (!supported || (pullRequests.length === 0 && !query.data?.error)) return null;

  return (
    <section aria-labelledby="pr-heading" className="border-b py-5">
      <div className="mb-3 flex items-center gap-2">
        <GitPullRequestIcon className="size-3.5 text-muted-foreground" />
        <h2 id="pr-heading" className="text-xs font-semibold tracking-[0.12em] uppercase">
          Pull requests
        </h2>
      </div>
      {query.data?.error && pullRequests.length === 0 ? (
        <p className="text-xs text-muted-foreground">{query.data.error}</p>
      ) : (
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {pullRequests.map((pullRequest) => (
            <ConductorPullRequest
              key={`${pullRequest.repository}#${pullRequest.number}`}
              pr={pullRequest}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ConductorPullRequest({ pr }: { pr: PullRequestSummary }) {
  const ci = {
    passing: { label: "CI passing", Icon: CheckCircle2Icon, className: "text-success" },
    pending: { label: "CI pending", Icon: Loader2Icon, className: "text-muted-foreground" },
    failing: { label: "CI failing", Icon: TriangleAlertIcon, className: "text-destructive" },
    unknown: { label: "CI unknown", Icon: CircleAlertIcon, className: "text-muted-foreground" },
  }[pr.ciStatus];
  return (
    <a
      href={pr.url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-w-0 items-center gap-2 text-xs hover:underline"
    >
      <span className="font-medium">#{pr.number}</span>
      <span className="max-w-48 truncate text-muted-foreground">{pr.branch}</span>
      <ci.Icon
        className={cn("size-3.5", ci.className, pr.ciStatus === "pending" && "animate-spin")}
      />
      <span className="sr-only">{ci.label}</span>
    </a>
  );
}

function PageState({
  label,
  detail,
  retry,
}: {
  label: string;
  detail?: string;
  retry?: () => void;
}) {
  return (
    <PageScroll contentClassName="px-6">
      <div className="flex min-h-72 flex-col items-center justify-center text-center">
        {retry ? (
          <TriangleAlertIcon className="mb-3 size-5 text-destructive" />
        ) : (
          <Loader2Icon className="mb-3 size-5 animate-spin text-muted-foreground" />
        )}
        <p className="text-sm font-medium">{label}</p>
        {detail && <p className="mt-1 max-w-md text-xs text-muted-foreground">{detail}</p>}
        {retry && (
          <Button className="mt-4" variant="outline" size="sm" onClick={retry}>
            Retry
          </Button>
        )}
      </div>
    </PageScroll>
  );
}
