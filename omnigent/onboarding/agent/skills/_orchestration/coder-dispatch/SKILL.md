---
name: coder-dispatch
description: Deprecated compatibility name for cloud-dispatch, which launches durable Codex or Claude Omnigent sessions on connected Coder boxes.
---

# Coder Dispatch (Compatibility)

`coder-dispatch` is deprecated and is expected to be removed in **v0.12.0**.
Use the canonical `cloud-dispatch` skill for all new invocations.

Load `cloud-dispatch` now and follow it exactly. That skill owns provider
selection, Coder discovery, explicit box pinning, worktree creation, retries,
and safety rules. Do not call `claude --remote`, do not dispatch locally, and
do not continue from remembered instructions if `cloud-dispatch` cannot be
loaded. Explain that the canonical skill is unavailable instead.
