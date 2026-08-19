import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConductorVoicePanel } from "./ConductorVoicePanel";
import * as conductorApi from "@/lib/conductorApi";
import * as conductorVoice from "@/lib/conductorVoice";

const voiceMocks = vi.hoisted(() => ({
  send: vi.fn(),
}));

vi.mock("@/components/ComposerMicButton", () => ({
  ComposerMicButton: ({ onTranscript }: { onTranscript: (text: string) => void }) => (
    <button type="button" onClick={() => onTranscript("Deploy this to production")}>
      Dictate test request
    </button>
  ),
}));

vi.mock("@/lib/conductorApi", async (importActual) => ({
  ...(await importActual<typeof conductorApi>()),
  updateConductorConfig: vi.fn(),
}));

vi.mock("@/lib/conductorVoice", async (importActual) => ({
  ...(await importActual<typeof conductorVoice>()),
  conductorVoiceProvider: () => ({
    id: "session-pipeline",
    label: "Session pipeline",
    description: "Dictation → your Conductor session → device speech",
    send: voiceMocks.send,
  }),
  speakConductorReply: vi.fn().mockResolvedValue(true),
  stopConductorSpeech: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  voiceMocks.send.mockResolvedValue({ itemId: "reply-1", text: "The release is ready to review." });
  vi.mocked(conductorApi.updateConductorConfig).mockResolvedValue({
    conversationId: "conductor-session",
    memoryProvider: "markdown",
    config: {},
    createdAt: 1,
    updatedAt: 2,
  });
});

afterEach(cleanup);

describe("ConductorVoicePanel", () => {
  it("requires a visible send tap and keeps consequential approval separate", async () => {
    render(<ConductorVoicePanel conversationId="conductor-session" config={{}} />);

    fireEvent.click(screen.getByRole("button", { name: "Voice briefing" }));
    fireEvent.click(screen.getByRole("button", { name: "Dictate test request" }));

    expect(screen.getByText(/This sounds consequential/)).toBeInTheDocument();
    expect(voiceMocks.send).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(voiceMocks.send).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: "conductor-session",
          text: "Deploy this to production",
          signal: expect.any(AbortSignal),
        }),
      ),
    );
    expect(await screen.findByText("The release is ready to review.")).toBeInTheDocument();
    expect(conductorVoice.speakConductorReply).toHaveBeenCalledWith(
      "The release is ready to review.",
      expect.objectContaining({ language: "en-US", rate: 1 }),
    );
    expect(screen.getByText(/cannot approve an elicitation by speech/i)).toBeInTheDocument();
  });
});
