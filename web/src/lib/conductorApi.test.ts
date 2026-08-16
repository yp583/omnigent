import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  bindConductor,
  getConductorDashboard,
  readConductorMemory,
  updateConductorConfig,
  updateConductorMemoryProvider,
  writeConductorMemory,
} from "./conductorApi";

function response(body: unknown, init?: { ok?: boolean; status?: number }): Response {
  return {
    ok: init?.ok ?? true,
    status: init?.status ?? 200,
    json: async () => body,
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("Conductor API", () => {
  it("parses the dashboard boundary into camelCase", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        conductor: {
          conversation_id: "session-conductor",
          memory_provider: "markdown",
          config: {},
          created_at: 10,
          updated_at: null,
        },
        memory_providers: ["markdown"],
        sessions: [
          {
            id: "session-1",
            title: "Ship it",
            status: "running",
            pending_approval_count: 2,
            created_at: 11,
            updated_at: 12,
            git_branch: "feature/demo",
          },
        ],
      }),
    );

    const dashboard = await getConductorDashboard();
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/conductor");
    expect(dashboard.conductor?.conversationId).toBe("session-conductor");
    expect(dashboard.sessions[0]).toMatchObject({
      pendingApprovalCount: 2,
      gitBranch: "feature/demo",
    });
  });

  it("binds an existing transcript with the Markdown provider", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        conversation_id: "session-1",
        memory_provider: "markdown",
        config: {},
        created_at: 10,
      }),
    );

    await bindConductor("session-1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      conversation_id: "session-1",
      memory_provider: "markdown",
    });
  });

  it("switches the provider without replacing the transcript", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        conversation_id: "session-1",
        memory_provider: "files",
        config: {},
        created_at: 10,
      }),
    );

    await updateConductorMemoryProvider("files");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/conductor");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ memory_provider: "files" });
  });

  it("persists provider-neutral Conductor settings", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        conversation_id: "session-1",
        memory_provider: "markdown",
        config: { voice: { provider: "session-pipeline", speakReplies: false } },
        created_at: 10,
      }),
    );

    const config = { voice: { provider: "session-pipeline", speakReplies: false } };
    await updateConductorConfig(config);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/conductor");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ config });
  });

  it("round-trips encoded paths and optimistic revisions", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          path: "projects/my plan/overview.md",
          revision: 2,
          checksum: "abc",
          created_at: 1,
          updated_at: 2,
          content: "# Plan",
        }),
      )
      .mockResolvedValueOnce(
        response({
          path: "MEMORY.md",
          revision: 4,
          checksum: "def",
          created_at: 1,
          updated_at: 3,
          content: "new",
        }),
      );

    const document = await readConductorMemory("projects/my plan/overview.md");
    expect(fetchMock.mock.calls[0][0]).toContain("path=projects%2Fmy+plan%2Foverview.md");
    expect(document.content).toBe("# Plan");

    await writeConductorMemory({ path: "MEMORY.md", content: "new", expectedRevision: 3 });
    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({ expected_revision: 3 });
  });

  it("surfaces a structured server conflict", async () => {
    fetchMock.mockResolvedValueOnce(
      response({ error: { message: "memory revision conflict" } }, { ok: false, status: 409 }),
    );
    await expect(
      writeConductorMemory({ path: "MEMORY.md", content: "stale", expectedRevision: 1 }),
    ).rejects.toThrow("revision conflict");
  });
});
