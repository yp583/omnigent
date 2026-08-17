# Omnigent Personal

This fork packages its own web frontend while continuing to use an existing
Omnigent cloud deployment as the API and session backend.

It is intentionally isolated from the official desktop application:

- app name: `Omnigent Personal`
- bundle id: `ai.omnigent.desktop.personal`
- macOS data directory: `~/Library/Application Support/Omnigent Personal`
- deep-link scheme: `omnigent-personal://`
- update feed: none; rebuild this checkout to update the personal app

Those identities also give the personal app a separate single-instance lock,
cookie jar, settings file, and login. `/Applications/Omnigent.app` can remain
installed and running while the personal build is tested.

## Run from the checkout

From the repository root:

```sh
pnpm install
pnpm --filter omnigent-desktop-electron dev
```

The pre-development step rebuilds both the main web bundle and the native
overlay. On first launch, enter the cloud URL in the connection screen and log
in. The personal app serves the checked-out frontend from a private loopback
origin and proxies only the backend API/auth namespaces to that cloud URL; it
does not deploy or mutate the cloud service.

The shell remembers a loopback port for each configured server. Keeping that
origin stable across launches preserves browser-local UI preferences such as
light/dark mode, color palette, panel sizes, and recent composer choices. If
another local process temporarily occupies the saved port, Personal chooses
and remembers a free replacement instead of failing to start.

PR cards use the local `git` and GitHub CLI installations. Authenticate once
with `gh auth login` if the card above the composer asks for it.

## Build a side-by-side macOS app

```sh
pnpm --filter web build
pnpm --filter web build:overlay
pnpm --filter omnigent-desktop-electron exec electron-builder --mac dir --arm64
```

The unpacked app is written under `web/electron/dist/` as
`Omnigent Personal.app`. Open it directly from there or copy that distinct app
to `/Applications`; do not rename it to `Omnigent.app`.
