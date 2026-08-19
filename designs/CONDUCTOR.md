# Conductor

Conductor is a user-private meta-session for supervising work across Omnigent.
It is not a new execution runtime: it is a normal, durable agent transcript with
an owner-scoped control plane, pluggable memory, and a focused operational UI.

## Product contract

- One active Conductor transcript per `(workspace, user)`.
- The active transcript must be a top-level session whose bound agent is named
  `conductor`; ordinary Claude, Codex, and custom-agent history is never
  eligible. The server enforces this independently of the UI.
- The dashboard lists top-level sessions the user owns plus sessions directly
  shared with that user. Public-link-only sessions never enter Conductor scope.
- The Conductor may read an owned or shared session and any descendant in that
  session's spawn tree. Steering requires edit-or-higher access; a read grant is
  never treated as control authority.
- Cross-session steering queues a normal user message in the target. The target
  remains independent; it is not registered as a fake Conductor child and does
  not fan results into the Conductor inbox.
- Merge, deploy, archive, stop, permission, destructive, and sensitive approval
  actions remain human-gated.
- The transcript is ordinary session history. Durable memory is a separate,
  provider-neutral collection of small Markdown documents.

## Runtime boundary

The built-in `conductor` agent receives the standard session inspection and
spawn tools plus three memory tools. Naming an arbitrary agent `conductor` is
not sufficient: every privileged runtime operation also proves that the caller
conversation is the user's active Conductor binding at the server.

For a target session, the server resolves its `root_conversation_id` and checks
the caller's current grant on that root for every read or steering request. A
foreign target returns the same not-found response as a missing target; a
read-only shared target returns an explicit non-steerable authorization result.
Single-user deployments treat local sessions as owner-scoped.

## Memory providers

`MemoryProviderRegistry` makes the selected provider a Conductor setting. The
initial `markdown` provider stores UTF-8 Markdown blobs in the configured
artifact store and keeps an owner-private SQL manifest with immutable revisions.
Paths are canonical relative `.md` paths; traversal, absolute paths, control
characters, and documents over 512 KiB are rejected. Writes use an expected
revision so concurrent edits produce a conflict instead of silent overwrite.

The canonical starter set is:

- `MEMORY.md`
- `profile/preferences.md`
- `skills/observations.md`

A future provider implements the same list/read/write/history/delete contract
and registers under a stable name. Switching providers never replaces the
Conductor transcript.

## Shared team session sources

The Conductor includes sessions that teammates explicitly share with the
current user. Shared sessions are additional permissioned sources, not a
widening to every session in a workspace.

- The existing direct session grant is the opt-in boundary. Merely belonging to
  a workspace and public-link access do not add a transcript to the ledger.
- Results retain source, owner, workspace, and sharing provenance so the UI and
  the Conductor can distinguish personal work from team context.
- Access is checked when listing and every time content is read. Revoking the
  underlying share removes the session from Conductor scope immediately.
- A read grant remains read-only. The existing edit-or-higher grant is the
  operator grant for steering; spawning, approval, and other consequential
  actions retain their separate gates.
- The shipped Conductor prompt forbids silently copying shared content into
  durable personal memory. An explicit user request is required, and saved
  material retains the source session id and owner as provenance.
- The dashboard supports personal-only, shared-with-me, and combined views,
  with the personal-only view as the safe default.

Team-collection opt-in and global cross-session content indexing remain future
work. The first implementation uses the existing workspace identity and direct,
durable session grants and performs targeted transcript reads through the
normal session authorization path.

## UI

`/conductor` provides:

- a quiet, status-first session ledger with inline steering and transcript links;
- a desktop PR rail backed by the existing native GitHub bridge;
- a Markdown memory desk with visible revisions and conflict errors;
- a provider selector when more than one backend is registered; and
- a responsive layout suitable for the mobile web shell.

The initial setup starts a dedicated transcript with the shipped Conductor
agent. Existing Conductor-agent sessions may be resumed, but ordinary session
history is never offered. Legacy invalid bindings are shown as unconfigured and
are repaired when the dedicated Conductor chat is created.

## Mobile voice

Voice is isolated on `personal/mobile-conductor-voice` and should land only
after this core passes human testing. The first registered provider is the
session pipeline: the existing browser/server dictation path supplies STT, a
normal visible message runs through the active Conductor transcript, and the
device supplies TTS. The web layer owns a small provider interface so a future
realtime provider can replace that route without changing the panel or the
Conductor authorization boundary. Voice preferences live under `config.voice`
and do not affect the selected memory provider.

The foreground panel is push-to-talk and review-before-send. Even an ordinary
dictated turn requires a Send tap. Requests that mention merge, deploy,
deletion, production, permissions, or approval get an additional warning;
speech never submits a runner elicitation. The Conductor's agent prompt treats
`[Voice request]` messages as spoken output and responds in short, listenable
sentences while directing consequential work back to on-screen approval cards.

iOS uses `AVSpeechSynthesizer`, Android uses `TextToSpeech`, and older native
shells fall back to the Web Speech API. Closing the panel cancels response
polling and active speech. The existing native notification path continues to
surface session completion and blocked/elicitation transitions while the app is
running. True background APNs/FCM delivery and proactive mobile PR polling need
device-token registration plus a deployment-owned push provider; they are not
silently represented as working by this foreground branch.
