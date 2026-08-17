import { render, screen } from "@testing-library/react";
import { Outlet, MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { FALLBACK_SERVER_INFO } from "@/lib/capabilities";
import { CapabilitiesProvider } from "@/lib/CapabilitiesContext";

vi.mock("@/lib/analytics", () => ({ useOmnigentPageView: vi.fn() }));
vi.mock("@/shell/AppShell", () => ({
  AppShell: () => (
    <div>
      <span>app shell</span>
      <Outlet />
    </div>
  ),
}));
vi.mock("@/pages/ChatPage", () => ({ ChatPage: () => <div>chat page</div> }));
vi.mock("@/pages/NotFoundPage", () => ({ NotFoundPage: () => <div>not found</div> }));
vi.mock("@/pages/UsagePage", () => ({ UsagePage: () => <div>usage page</div> }));
vi.mock("@/pages/ConductorPage", () => ({
  ConductorPage: () => <div>conductor setup</div>,
}));
vi.mock("@/lib/conductorApi", () => ({ getConductorDashboard: vi.fn() }));

import App from "./App";
import { getConductorDashboard } from "@/lib/conductorApi";

function renderRoute(path: string, info: typeof FALLBACK_SERVER_INFO = FALLBACK_SERVER_INFO) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CapabilitiesProvider info={info}>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </CapabilitiesProvider>
    </QueryClientProvider>,
  );
}

function renderUsageRoute(enabled: boolean) {
  const info: typeof FALLBACK_SERVER_INFO = {
    ...FALLBACK_SERVER_INFO,
    features: enabled ? { usage_page: true } : {},
  };
  return renderRoute("/usage", info);
}

describe("Usage release feature route", () => {
  it("does not register /usage while the feature is off", async () => {
    renderUsageRoute(false);
    expect(await screen.findByText("not found")).toBeInTheDocument();
    expect(screen.queryByText("usage page")).toBeNull();
  });

  it("registers /usage while the feature is on", async () => {
    renderUsageRoute(true);
    expect(await screen.findByText("usage page")).toBeInTheDocument();
    expect(screen.queryByText("not found")).toBeNull();
  });
});

describe("Conductor chat route", () => {
  it("renders only the validated active Conductor transcript", async () => {
    vi.mocked(getConductorDashboard).mockResolvedValue({
      conductor: {
        conversationId: "conductor-session",
        memoryProvider: "markdown",
        config: {},
        createdAt: 1,
        updatedAt: null,
      },
      memoryProviders: ["markdown"],
      sessions: [],
    });

    renderRoute("/conductor/conductor-session");
    expect(await screen.findByText("chat page")).toBeInTheDocument();
  });

  it("redirects a stale ordinary-transcript deep link to setup", async () => {
    vi.mocked(getConductorDashboard).mockResolvedValue({
      conductor: null,
      memoryProviders: ["markdown"],
      sessions: [],
    });

    renderRoute("/conductor/ordinary-session");
    expect(await screen.findByText("conductor setup")).toBeInTheDocument();
    expect(screen.queryByText("chat page")).toBeNull();
  });
});
