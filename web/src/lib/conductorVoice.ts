import { isMessageItem, type ConversationItem } from "@/lib/conversationItems";
import { nativeSpeak, stopNativeSpeech } from "@/lib/nativeBridge";
import { fetchSessionItemsPage, postEvent, type SessionItemsPage } from "@/lib/sessionsApi";

export const SESSION_PIPELINE_VOICE_PROVIDER = "session-pipeline";

export interface ConductorVoiceSettings {
  provider: string;
  speakReplies: boolean;
  language: string;
  rate: number;
}

export interface ConductorVoiceReply {
  itemId: string;
  text: string;
}

export interface ConductorVoiceProvider {
  id: string;
  label: string;
  description: string;
  send: (input: {
    sessionId: string;
    text: string;
    signal: AbortSignal;
  }) => Promise<ConductorVoiceReply>;
}

const DEFAULT_SETTINGS: ConductorVoiceSettings = {
  provider: SESSION_PIPELINE_VOICE_PROVIDER,
  speakReplies: true,
  language: "en-US",
  rate: 1,
};

const POLL_INTERVAL_MS = 1_000;
const REPLY_TIMEOUT_MS = 180_000;

type PageLoader = (sessionId: string, options?: { limit?: number }) => Promise<SessionItemsPage>;

/** Read the provider-neutral voice section without trusting stored JSON. */
export function conductorVoiceSettings(config: Record<string, unknown>): ConductorVoiceSettings {
  const raw = isRecord(config.voice) ? config.voice : {};
  return {
    provider:
      typeof raw.provider === "string" && raw.provider.trim()
        ? raw.provider
        : DEFAULT_SETTINGS.provider,
    speakReplies:
      typeof raw.speakReplies === "boolean" ? raw.speakReplies : DEFAULT_SETTINGS.speakReplies,
    language:
      typeof raw.language === "string" && raw.language.trim()
        ? raw.language
        : DEFAULT_SETTINGS.language,
    rate:
      typeof raw.rate === "number" && Number.isFinite(raw.rate)
        ? Math.max(0.5, Math.min(2, raw.rate))
        : DEFAULT_SETTINGS.rate,
  };
}

/** Merge voice preferences without discarding another Conductor provider's config. */
export function withConductorVoiceSettings(
  config: Record<string, unknown>,
  settings: ConductorVoiceSettings,
): Record<string, unknown> {
  return {
    ...config,
    voice: {
      provider: settings.provider,
      speakReplies: settings.speakReplies,
      language: settings.language,
      rate: settings.rate,
    },
  };
}

/** The final assistant message in a chronological page, if one exists. */
export function latestAssistantReply(items: ConversationItem[]): ConductorVoiceReply | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (!isMessageItem(item) || item.role !== "assistant") continue;
    const text = item.content
      .filter(
        (block): block is { type: "output_text"; text: string } => block.type === "output_text",
      )
      .map((block) => block.text)
      .join("")
      .trim();
    if (text) return { itemId: item.id, text };
  }
  return null;
}

/**
 * Speech never approves a consequential operation. This detector only changes
 * the review copy; every dictated turn still requires an explicit Send tap,
 * and actual runner elicitations remain separate approval cards.
 */
export function isConsequentialVoiceIntent(text: string): boolean {
  return /\b(approve|merge|deploy|release|publish|archive|delete|destroy|grant|permission|production|prod|stop\s+(?:the\s+)?session)\b/i.test(
    text,
  );
}

/** Convert common Markdown into calmer speech without trying to render it. */
export function speechText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " Code block omitted. ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_~>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Speak through the native phone shell first, then Web Speech as fallback. */
export async function speakConductorReply(
  text: string,
  settings: Pick<ConductorVoiceSettings, "language" | "rate">,
): Promise<boolean> {
  const spoken = speechText(text).slice(0, 8_000);
  if (!spoken) return false;
  stopConductorSpeech();
  if (await nativeSpeak({ text: spoken, language: settings.language, rate: settings.rate })) {
    return true;
  }
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return false;
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.lang = settings.language;
  utterance.rate = settings.rate;
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopConductorSpeech(): void {
  stopNativeSpeech();
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
}

/**
 * STT → normal Conductor session → TTS provider. It intentionally uses the
 * same session endpoint as typed chat, preserving every ownership and approval
 * check already enforced by Conductor.
 */
export function createSessionPipelineVoiceProvider(
  loadPage: PageLoader = fetchSessionItemsPage,
): ConductorVoiceProvider {
  return {
    id: SESSION_PIPELINE_VOICE_PROVIDER,
    label: "Session pipeline",
    description: "Dictation → your Conductor session → device speech",
    async send({ sessionId, text, signal }) {
      const before = latestAssistantReply((await loadPage(sessionId, { limit: 20 })).items);
      if (signal.aborted) throw abortError();
      const accepted = await postEvent(sessionId, {
        type: "message",
        data: {
          role: "user",
          content: [{ type: "input_text", text: `[Voice request]\n${text}` }],
        },
      });
      if (accepted.denied) throw new Error("The voice request was denied by session policy.");
      return waitForNewAssistantReply(sessionId, before?.itemId ?? null, signal, loadPage);
    },
  };
}

const conductorVoiceProviderRegistry = new Map<string, ConductorVoiceProvider>();
const sessionPipelineProvider = createSessionPipelineVoiceProvider();
conductorVoiceProviderRegistry.set(sessionPipelineProvider.id, sessionPipelineProvider);

/** Register a foreground voice transport without changing the panel contract. */
export function registerConductorVoiceProvider(provider: ConductorVoiceProvider): void {
  if (!provider.id.trim()) throw new Error("Conductor voice provider id is required");
  conductorVoiceProviderRegistry.set(provider.id, provider);
}

export function listConductorVoiceProviders(): ConductorVoiceProvider[] {
  return [...conductorVoiceProviderRegistry.values()];
}

export function conductorVoiceProvider(id: string): ConductorVoiceProvider {
  return conductorVoiceProviderRegistry.get(id) ?? sessionPipelineProvider;
}

export async function waitForNewAssistantReply(
  sessionId: string,
  priorItemId: string | null,
  signal: AbortSignal,
  loadPage: PageLoader = fetchSessionItemsPage,
): Promise<ConductorVoiceReply> {
  const deadline = Date.now() + REPLY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (signal.aborted) throw abortError();
    // oxlint-disable-next-line eslint/no-await-in-loop -- Each read must follow the prior delay; parallel polling would stampede the session endpoint.
    const reply = latestAssistantReply((await loadPage(sessionId, { limit: 20 })).items);
    if (reply && reply.itemId !== priorItemId) return reply;
    // oxlint-disable-next-line eslint/no-await-in-loop -- Deliberately serial polling with cancellation.
    await abortableDelay(POLL_INTERVAL_MS, signal);
  }
  throw new Error("Conductor is still working. Open the transcript to follow the response.");
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const handleAbort = () => {
      window.clearTimeout(timer);
      reject(abortError());
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function abortError(): DOMException {
  return new DOMException("Voice request cancelled", "AbortError");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
