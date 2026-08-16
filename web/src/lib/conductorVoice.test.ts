import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ConversationItem } from "./conversationItems";
import {
  conductorVoiceSettings,
  createSessionPipelineVoiceProvider,
  isConsequentialVoiceIntent,
  latestAssistantReply,
  listConductorVoiceProviders,
  registerConductorVoiceProvider,
  speechText,
  withConductorVoiceSettings,
} from "./conductorVoice";
import * as sessionsApi from "./sessionsApi";

vi.mock("./sessionsApi", async (importActual) => ({
  ...(await importActual<typeof sessionsApi>()),
  postEvent: vi.fn(),
}));

function assistant(id: string, text: string): ConversationItem {
  return {
    id,
    type: "message",
    role: "assistant",
    response_id: `response-${id}`,
    status: "completed",
    content: [{ type: "output_text", text }],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(sessionsApi.postEvent).mockResolvedValue({ queued: true });
});

describe("Conductor voice settings", () => {
  it("uses safe defaults and preserves unrelated provider config", () => {
    const settings = conductorVoiceSettings({ project: "alpha" });
    expect(settings).toEqual({
      provider: "session-pipeline",
      speakReplies: true,
      language: "en-US",
      rate: 1,
    });
    expect(withConductorVoiceSettings({ project: "alpha" }, settings)).toEqual({
      project: "alpha",
      voice: settings,
    });
  });

  it("bounds stored speech rate", () => {
    expect(conductorVoiceSettings({ voice: { rate: 12 } }).rate).toBe(2);
    expect(conductorVoiceSettings({ voice: { rate: 0 } }).rate).toBe(0.5);
  });

  it("accepts a replacement transport behind the provider-neutral contract", () => {
    registerConductorVoiceProvider({
      id: "test-realtime",
      label: "Test realtime",
      description: "Test transport",
      send: vi.fn(),
    });
    expect(listConductorVoiceProviders().map((provider) => provider.id)).toContain("test-realtime");
  });
});

describe("Conductor voice safety and speech", () => {
  it("flags consequential requests but leaves ordinary questions alone", () => {
    expect(isConsequentialVoiceIntent("Deploy this to production")).toBe(true);
    expect(isConsequentialVoiceIntent("What is going well today?")).toBe(false);
  });

  it("removes noisy Markdown before speech", () => {
    expect(
      speechText("## Status\n- **Ready**: [open PR](https://example.test)\n```sh\ngh pr\n```"),
    ).toBe("Status Ready: open PR Code block omitted.");
  });

  it("finds the newest assistant text", () => {
    expect(latestAssistantReply([assistant("old", "Earlier"), assistant("new", "Latest")])).toEqual(
      { itemId: "new", text: "Latest" },
    );
  });
});

describe("session pipeline provider", () => {
  it("sends a visible voice request and waits for a new assistant item", async () => {
    const loadPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [assistant("before", "Old")], hasMore: false })
      .mockResolvedValueOnce({ items: [assistant("after", "Fresh status")], hasMore: false });
    const provider = createSessionPipelineVoiceProvider(loadPage);

    await expect(
      provider.send({
        sessionId: "conductor-session",
        text: "What needs me?",
        signal: new AbortController().signal,
      }),
    ).resolves.toEqual({ itemId: "after", text: "Fresh status" });

    expect(sessionsApi.postEvent).toHaveBeenCalledWith("conductor-session", {
      type: "message",
      data: {
        role: "user",
        content: [{ type: "input_text", text: "[Voice request]\nWhat needs me?" }],
      },
    });
  });

  it("does not poll when policy denies the request", async () => {
    vi.mocked(sessionsApi.postEvent).mockResolvedValueOnce({ queued: false, denied: true });
    const loadPage = vi.fn().mockResolvedValue({ items: [], hasMore: false });
    const provider = createSessionPipelineVoiceProvider(loadPage);

    await expect(
      provider.send({
        sessionId: "conductor-session",
        text: "hello",
        signal: new AbortController().signal,
      }),
    ).rejects.toThrow("denied by session policy");
    expect(loadPage).toHaveBeenCalledTimes(1);
  });
});
