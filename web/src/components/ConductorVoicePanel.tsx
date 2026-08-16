import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudioLinesIcon,
  CheckIcon,
  Loader2Icon,
  SendIcon,
  ShieldCheckIcon,
  Volume2Icon,
  VolumeXIcon,
} from "lucide-react";

import { ComposerMicButton } from "@/components/ComposerMicButton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { updateConductorConfig } from "@/lib/conductorApi";
import {
  conductorVoiceProvider,
  conductorVoiceSettings,
  isConsequentialVoiceIntent,
  listConductorVoiceProviders,
  speakConductorReply,
  stopConductorSpeech,
  withConductorVoiceSettings,
  type ConductorVoiceReply,
} from "@/lib/conductorVoice";
import { cn } from "@/lib/utils";

type VoicePhase = "ready" | "listening" | "waiting" | "reply";

interface ConductorVoicePanelProps {
  conversationId: string;
  config: Record<string, unknown>;
  onConfigUpdated?: () => void;
}

export function ConductorVoicePanel({
  conversationId,
  config,
  onConfigUpdated,
}: ConductorVoicePanelProps) {
  const storedSettings = useMemo(() => conductorVoiceSettings(config), [config]);
  const [settings, setSettings] = useState(storedSettings);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [interim, setInterim] = useState("");
  const [phase, setPhase] = useState<VoicePhase>("ready");
  const [reply, setReply] = useState<ConductorVoiceReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const voiceStartDraftRef = useRef("");

  useEffect(() => setSettings(storedSettings), [storedSettings]);
  useEffect(
    () => () => {
      requestRef.current?.abort();
      stopConductorSpeech();
    },
    [],
  );

  const provider = conductorVoiceProvider(settings.provider);
  const providers = listConductorVoiceProviders();
  const consequential = isConsequentialVoiceIntent(`${draft} ${interim}`);
  const busy = phase === "waiting";

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      requestRef.current?.abort();
      requestRef.current = null;
      stopConductorSpeech();
      setInterim("");
      setPhase("ready");
    }
  }

  function appendTranscript(text: string) {
    setDraft((current) => `${current}${current.trim() ? " " : ""}${text}`);
    setInterim("");
  }

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    requestRef.current?.abort();
    stopConductorSpeech();
    const controller = new AbortController();
    requestRef.current = controller;
    setError(null);
    setReply(null);
    setPhase("waiting");
    try {
      const nextReply = await provider.send({
        sessionId: conversationId,
        text,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setReply(nextReply);
      setDraft("");
      setPhase("reply");
      if (settings.speakReplies) {
        await speakConductorReply(nextReply.text, settings);
      }
    } catch (sendError) {
      if (sendError instanceof DOMException && sendError.name === "AbortError") return;
      setError(sendError instanceof Error ? sendError.message : "Voice request failed");
      setPhase("ready");
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
    }
  }

  async function persistSettings(next: typeof settings) {
    setSettings(next);
    setSettingsSaved(false);
    try {
      await updateConductorConfig(withConductorVoiceSettings(config, next));
      setSettingsSaved(true);
      window.setTimeout(() => setSettingsSaved(false), 1_500);
      onConfigUpdated?.();
    } catch (saveError) {
      setSettings(storedSettings);
      setError(saveError instanceof Error ? saveError.message : "Voice setting could not be saved");
    }
  }

  function setSpeakReplies(checked: boolean) {
    if (!checked) stopConductorSpeech();
    void persistSettings({ ...settings, speakReplies: checked });
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <AudioLinesIcon className="size-4" /> Voice briefing
        </Button>
      </DialogTrigger>
      <DialogContent
        className="max-h-[min(46rem,calc(100vh-1rem))] overflow-y-auto sm:max-w-xl"
        data-testid="conductor-voice-panel"
      >
        <DialogHeader>
          <DialogTitle>Talk with Conductor</DialogTitle>
          <DialogDescription>
            Push to talk, review the transcript, then send. Replies can play through your device.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-4 border-y py-3 text-xs">
          <div className="min-w-0">
            {providers.length > 1 ? (
              <Select
                value={provider.id}
                onValueChange={(providerId) =>
                  void persistSettings({ ...settings, provider: providerId })
                }
              >
                <SelectTrigger size="sm" aria-label="Voice provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {providers.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p className="font-medium">{provider.label}</p>
            )}
            <p className="mt-0.5 truncate text-muted-foreground">{provider.description}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <label htmlFor="conductor-speak-replies" className="text-muted-foreground">
              Speak replies
            </label>
            {settingsSaved && <CheckIcon className="size-3.5 text-success" aria-label="Saved" />}
            <Switch
              id="conductor-speak-replies"
              size="sm"
              checked={settings.speakReplies}
              onCheckedChange={setSpeakReplies}
            />
          </div>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-medium">Your request</span>
            <span className="text-muted-foreground" aria-live="polite">
              {phaseLabel(phase)}
            </span>
          </div>
          <div
            className={cn(
              "rounded-xl border bg-background focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20",
              phase === "listening" && "border-foreground/35",
            )}
          >
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="What needs attention across my sessions?"
              aria-label="Voice request transcript"
              rows={4}
              disabled={busy}
              className="min-h-28 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            />
            {interim && (
              <p className="px-3 pb-2 text-xs text-muted-foreground" aria-live="polite">
                {interim}
              </p>
            )}
            <div className="flex items-center justify-between border-t px-2 py-2">
              <ComposerMicButton
                disabled={busy}
                lang={settings.language}
                onTranscript={appendTranscript}
                onInterim={setInterim}
                onVoiceStart={() => {
                  voiceStartDraftRef.current = draft;
                }}
                onVoiceDiscard={() => {
                  setDraft(voiceStartDraftRef.current);
                  setInterim("");
                }}
                onListeningChange={(listening) =>
                  setPhase((current) =>
                    current === "waiting" || current === "reply"
                      ? current
                      : listening
                        ? "listening"
                        : "ready",
                  )
                }
              />
              <Button size="sm" disabled={!draft.trim() || busy} onClick={() => void send()}>
                {busy ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <SendIcon className="size-4" />
                )}
                {consequential ? "Send request" : "Send"}
              </Button>
            </div>
          </div>
        </div>

        {consequential && (
          <div className="flex gap-2.5 rounded-lg border border-brand-accent/30 bg-brand-accent/8 p-3 text-xs leading-5">
            <ShieldCheckIcon className="mt-0.5 size-4 shrink-0 text-brand-accent" />
            <p>
              This sounds consequential. Sending asks Conductor to prepare or explain it; merge,
              deploy, deletion, permission, and runner approvals still require a separate on-screen
              tap.
            </p>
          </div>
        )}

        {error && (
          <p
            role="alert"
            className="rounded-lg border border-destructive/30 p-3 text-xs text-destructive"
          >
            {error}
          </p>
        )}

        {reply && (
          <section aria-labelledby="voice-reply-heading" className="border-t pt-4">
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 id="voice-reply-heading" className="text-xs font-medium">
                Conductor
              </h3>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => void speakConductorReply(reply.text, settings)}
                >
                  <Volume2Icon className="size-3.5" /> Replay
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Stop speaking"
                  onClick={stopConductorSpeech}
                >
                  <VolumeXIcon className="size-3.5" />
                </Button>
              </div>
            </div>
            <p className="max-h-52 overflow-y-auto whitespace-pre-wrap text-sm leading-6">
              {reply.text}
            </p>
          </section>
        )}

        <div className="flex items-start gap-2 border-t pt-3 text-[11px] leading-4 text-muted-foreground">
          <ShieldCheckIcon className="mt-px size-3.5 shrink-0" />
          <span>
            Voice uses the same private Conductor session and permissions as typed chat. It cannot
            approve an elicitation by speech.
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function phaseLabel(phase: VoicePhase): string {
  if (phase === "listening") return "Listening…";
  if (phase === "waiting") return "Conductor is working…";
  if (phase === "reply") return "Reply ready";
  return "Ready";
}
