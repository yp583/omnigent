import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ensureConductor } from "@/lib/conductorApi";
import { ConductorGate } from "./ConductorGate";

vi.mock("@/lib/conductorApi", () => ({ ensureConductor: vi.fn() }));

function renderGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/conductor"]}>
        <Routes>
          <Route path="/conductor" element={<ConductorGate />} />
          <Route path="/conductor/:conversationId" element={<div>Conductor chat</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.mocked(ensureConductor).mockReset());

describe("ConductorGate", () => {
  it("opens the ensured singleton transcript", async () => {
    vi.mocked(ensureConductor).mockResolvedValue({
      conversationId: "conductor-1",
      memoryProvider: "markdown",
      config: {},
      createdAt: 1,
      updatedAt: null,
    });

    renderGate();

    expect(await screen.findByText("Conductor chat")).toBeInTheDocument();
    expect(ensureConductor).toHaveBeenCalledTimes(1);
  });

  it("shows a useful error and retries in place", async () => {
    vi.mocked(ensureConductor)
      .mockRejectedValueOnce(new Error("Start any normal session once, then retry."))
      .mockResolvedValueOnce({
        conversationId: "conductor-1",
        memoryProvider: "markdown",
        config: {},
        createdAt: 1,
        updatedAt: null,
      });

    renderGate();
    expect(await screen.findByRole("alert")).toHaveTextContent("Start any normal session");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(ensureConductor).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Conductor chat")).toBeInTheDocument();
  });
});
