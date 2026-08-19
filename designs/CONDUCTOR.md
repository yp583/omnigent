# Conductor

Conductor is a user-private chat agent that supervises work across Omnigent. It
is not a dashboard and it is not a new execution runtime. It is one ordinary,
durable transcript backed by the existing session runtime, plus a narrow set of
typed, server-authorized operator tools and pluggable long-term memory.

## Product contract

- `/conductor` creates the caller's Conductor on first open and resumes that
  same transcript thereafter. There is no setup screen or agent picker.
- The Conductor conversation is top-level, bound to the built-in `conductor`
  agent, labelled `omnigent.conductor=true`, and omitted from ordinary sidebar
  session rows. The dedicated Conductor navigation item is its only primary
  entry point.
- First creation reuses the runner and host affinity of the caller's newest
  owned runner-backed session. If no such session exists, the UI explains that
  the user must start one normal session once and offers an in-place retry.
- An existing valid binding is idempotently reused. An old binding to an
  ordinary transcript is invalid and is repaired with a new dedicated chat.
- The transcript uses the normal chat surface: streaming, history, attachments,
  approvals, compaction, and the composer all behave like another agent chat.
- No separate dashboard, session tree, status grid, PR rail, memory editor, or
  special voice panel is mounted inside the Conductor route.

## Scope and authorization

Conductor can inspect top-level sessions the user owns and sessions directly
shared with that user. Public-link-only sessions are excluded. Descendants are
read through their permission root, so a shared root's sub-agent transcript is
available only while the caller retains the root grant.

- Read access permits metadata and transcript inspection.
- Edit-or-higher access permits steering and ordinary session mutations.
- Manage access is required for changing another user's grant.
- Every privileged runtime request independently verifies both the active
  Conductor binding and the target's current server-side permission. Naming an
  arbitrary custom agent `conductor` grants nothing.
- Foreign and missing sessions have the same not-found behavior.
- The Conductor cannot target its own session.

Shared transcript content is untrusted input. The system prompt tells the agent
to treat instructions found inside another session as data, not authority, and
to avoid copying shared content into personal memory unless the user explicitly
asks for that.

## Operator tools

The built-in agent receives the existing typed session, agent, policy,
scheduled-task, browser, and spawn tools. Conductor-only tools add:

- rename, archive, restore, and stop for accessible sessions;
- grant/change/revoke session permissions;
- list/create/update/delete projects;
- read/update Conductor settings; and
- list/read/write durable memory.

These tools call authenticated Omnigent server endpoints; Conductor receives no
general-purpose shell tool. The transcript renders their calls as compact
plain-English action rows, with the complete request and server response behind
the normal disclosure affordance.

## Approval modes

The composer gear exposes the same Claude approval-mode choices as other Claude
sessions. The selected value is stored in the per-user Conductor binding and is
overlaid when its harness starts; the shared built-in agent bundle is never
mutated.

`default` is the initial mode. In that mode, read-only discovery and memory
reads are pre-approved so routine supervision does not produce approval spam,
while mutations pass through the existing elicitation flow. Changing the mode
is owner-only, requires an idle session, and restarts the harness without adding
a transcript message. Dangerous modes retain the existing warning treatment.

## Memory providers

`MemoryProviderRegistry` makes memory provider selection a per-user Conductor
setting. The initial `markdown` provider stores UTF-8 Markdown blobs in the
configured artifact store and keeps an owner-private SQL manifest with
immutable revisions. Paths are canonical relative `.md` paths; traversal,
absolute paths, control characters, and documents over 512 KiB are rejected.
Writes use an expected revision so concurrent changes conflict instead of
silently overwriting one another.

The starter set is:

- `MEMORY.md`
- `profile/preferences.md`
- `skills/observations.md`

A future provider implements the same list/read/write/history/delete contract
and registers under a stable name. Switching provider does not replace or
rewrite the Conductor transcript.

## Deliberate follow-ups

Team-wide collections, organization-level transcript indexing, proactive
background notifications, and a realtime STT/LLM/TTS chief-of-staff experience
remain separate follow-ups. The ordinary composer microphone remains available
on mobile, but this release does not claim background voice operation or add a
second Conductor-specific interaction model.
