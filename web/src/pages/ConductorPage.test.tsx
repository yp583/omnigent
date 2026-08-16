import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConductorPage } from "./ConductorPage";
import * as conductorApi from "@/lib/conductorApi";
import * as nativeBridge from "@/lib/nativeBridge";
import * as sessionsApi from "@/lib/sessionsApi";

vi.mock("@/lib/conductorApi", async (importActual) => ({
  ...(await importActual<typeof conductorApi>()),
  getConductorDashboard: vi.fn(),
  bindConductor: vi.fn(),
  listConductorMemory: vi.fn(),
  readConductorMemory: vi.fn(),
  writeConductorMemory: vi.fn(),
}));
vi.mock("@/lib/nativeBridge", async (importActual) => ({
  ...(await importActual<typeof nativeBridge>()),
  supportsPullRequestTracking: vi.fn(() => false),
  listNativePullRequests: vi.fn(),
}));
vi.mock("@/lib/sessionsApi", async (importActual) => ({
  ...(await importActual<typeof sessionsApi>()),
  postEvent: vi.fn(),
}));

const session: conductorApi.ConductorSession = {
  id: "session-work",
  title: "Fix onboarding",
  status: "running",
  pendingApprovalCount: 0,
  updatedAt: 1_700_000_100,
  createdAt: 1_700_000_000,
  workspace: "/repo",
  gitBranch: "feature/onboarding",
  taskSummary: null,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConductorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderRoutedPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/conductor"]}>
        <Routes>
          <Route path="/conductor" element={<ConductorPage />} />
          <Route path="/conductor/:conversationId" element={<div>Conductor chat</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(nativeBridge.supportsPullRequestTracking).mockReturnValue(false);
  vi.mocked(conductorApi.listConductorMemory).mockResolvedValue([
    {
      path: "MEMORY.md",
      revision: 1,
      checksum: "abc",
      createdAt: 1,
      updatedAt: 1,
    },
  ]);
  vi.mocked(conductorApi.readConductorMemory).mockResolvedValue({
    path: "MEMORY.md",
    revision: 1,
    checksum: "abc",
    createdAt: 1,
    updatedAt: 1,
    content: "# Memory",
  });
  vi.mocked(sessionsApi.postEvent).mockResolvedValue({ queued: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ConductorPage", () => {
  it("lets the user designate an owned session", async () => {
    vi.mocked(conductorApi.getConductorDashboard).mockResolvedValue({
      conductor: null,
      memoryProviders: ["markdown"],
      sessions: [session],
    });
    vi.mocked(conductorApi.bindConductor).mockResolvedValue({
      conversationId: session.id,
      memoryProvider: "markdown",
      config: {},
      createdAt: 1,
      updatedAt: null,
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Fix onboarding/ }));
    fireEvent.click(screen.getByRole("button", { name: "Make Conductor" }));
    await waitFor(() => expect(conductorApi.bindConductor).toHaveBeenCalledWith(session.id));
  });

  it("opens the bound Conductor as a chat", async () => {
    vi.mocked(conductorApi.getConductorDashboard).mockResolvedValue({
      conductor: {
        conversationId: "conductor-session",
        memoryProvider: "markdown",
        config: {},
        createdAt: 1,
        updatedAt: null,
      },
      memoryProviders: ["markdown"],
      sessions: [session],
    });
    renderRoutedPage();

    expect(await screen.findByText("Conductor chat")).toBeInTheDocument();
  });
});
