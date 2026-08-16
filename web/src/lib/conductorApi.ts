import { authenticatedFetch } from "@/lib/identity";

export interface ConductorBinding {
  conversationId: string;
  memoryProvider: string;
  config: Record<string, unknown>;
  createdAt: number;
  updatedAt: number | null;
}

export interface ConductorSession {
  id: string;
  title: string | null;
  status: "idle" | "running" | "waiting" | "failed" | string;
  pendingApprovalCount: number;
  updatedAt: number;
  createdAt: number;
  workspace: string | null;
  gitBranch: string | null;
  taskSummary: string | null;
}

export interface ConductorDashboard {
  conductor: ConductorBinding | null;
  memoryProviders: string[];
  sessions: ConductorSession[];
}

export interface ConductorMemoryDocument {
  path: string;
  revision: number;
  checksum: string;
  createdAt: number;
  updatedAt: number;
  content?: string;
}

interface BindingWire {
  conversation_id: string;
  memory_provider: string;
  config: Record<string, unknown>;
  created_at: number;
  updated_at?: number | null;
}

interface SessionWire {
  id: string;
  title?: string | null;
  status: string;
  pending_approval_count?: number;
  updated_at: number;
  created_at: number;
  workspace?: string | null;
  git_branch?: string | null;
  task_summary?: string | null;
}

interface MemoryWire {
  path: string;
  revision: number;
  checksum: string;
  created_at: number;
  updated_at: number;
  content?: string;
}

function parseBinding(wire: BindingWire): ConductorBinding {
  return {
    conversationId: wire.conversation_id,
    memoryProvider: wire.memory_provider,
    config: wire.config,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at ?? null,
  };
}

function parseSession(wire: SessionWire): ConductorSession {
  return {
    id: wire.id,
    title: wire.title ?? null,
    status: wire.status,
    pendingApprovalCount: wire.pending_approval_count ?? 0,
    updatedAt: wire.updated_at,
    createdAt: wire.created_at,
    workspace: wire.workspace ?? null,
    gitBranch: wire.git_branch ?? null,
    taskSummary: wire.task_summary ?? null,
  };
}

function parseMemory(wire: MemoryWire): ConductorMemoryDocument {
  return {
    path: wire.path,
    revision: wire.revision,
    checksum: wire.checksum,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    ...(wire.content !== undefined ? { content: wire.content } : {}),
  };
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `Request failed (${response.status})`;
  try {
    const body = (await response.json()) as {
      error?: { message?: string };
      detail?: string;
    };
    message = body.error?.message ?? body.detail ?? message;
  } catch {
    // Keep the status-derived fallback for non-JSON proxy/server failures.
  }
  throw new Error(message);
}

export async function getConductorDashboard(): Promise<ConductorDashboard> {
  const wire = await readJson<{
    conductor: BindingWire | null;
    memory_providers: string[];
    sessions: SessionWire[];
  }>(await authenticatedFetch("/v1/conductor"));
  return {
    conductor: wire.conductor ? parseBinding(wire.conductor) : null,
    memoryProviders: wire.memory_providers,
    sessions: wire.sessions.map(parseSession),
  };
}

export async function bindConductor(
  conversationId: string,
  memoryProvider = "markdown",
): Promise<ConductorBinding> {
  const wire = await readJson<BindingWire>(
    await authenticatedFetch("/v1/conductor", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        memory_provider: memoryProvider,
      }),
    }),
  );
  return parseBinding(wire);
}

export async function updateConductorMemoryProvider(
  memoryProvider: string,
): Promise<ConductorBinding> {
  const wire = await readJson<BindingWire>(
    await authenticatedFetch("/v1/conductor", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ memory_provider: memoryProvider }),
    }),
  );
  return parseBinding(wire);
}

export async function updateConductorConfig(
  config: Record<string, unknown>,
): Promise<ConductorBinding> {
  const wire = await readJson<BindingWire>(
    await authenticatedFetch("/v1/conductor", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    }),
  );
  return parseBinding(wire);
}

export async function listConductorMemory(): Promise<ConductorMemoryDocument[]> {
  const wire = await readJson<{ data: MemoryWire[] }>(
    await authenticatedFetch("/v1/conductor/memory"),
  );
  return wire.data.map(parseMemory);
}

export async function readConductorMemory(path: string): Promise<ConductorMemoryDocument> {
  const query = new URLSearchParams({ path });
  return parseMemory(
    await readJson<MemoryWire>(
      await authenticatedFetch(`/v1/conductor/memory/document?${query.toString()}`),
    ),
  );
}

export async function writeConductorMemory(input: {
  path: string;
  content: string;
  expectedRevision: number;
}): Promise<ConductorMemoryDocument> {
  const wire = await readJson<MemoryWire>(
    await authenticatedFetch("/v1/conductor/memory/document", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: input.path,
        content: input.content,
        expected_revision: input.expectedRevision,
      }),
    }),
  );
  return parseMemory(wire);
}
