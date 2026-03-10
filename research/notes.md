# Windows-MCP Reliability Notes

Date: 2026-03-10
Repo: `/mnt/c/Users/Rufus/src/Windows-MCP`

## Goal

- Improve reliability, repeatability, and debuggability of Windows desktop automation used by Codex on a real Windows machine.

## Current State

- Native launch / switch / typing issues found early in the session now have code fixes, tests, and live retests.
- Hot mode is usable again for real research:
  - long forwarded calls work through the persistent stdio worker bridge
  - `DevServer.health` and `DevServer.reload` now return promptly
- `Snapshot` is stronger now:
  - native informative text is exposed
  - stale cached label state is rejected after 10 seconds
- Browser and Settings routing is clearer now:
  - browser extraction should prefer DOM
  - Settings navigation should prefer protocol URIs such as `ms-settings:*`

## High-Value Proven Fixes

- `App.launch` no longer reports false negatives just because the eventual window title differs from the requested app name.
- `App.switch` now fails closed when focus did not actually change, and the focus fallback path is stronger.
- Long forwarded hot-mode calls no longer hang on the old worker HTTP path because hot mode now uses a persistent stdio bridge.
- `Scrape(use_dom=true)` now reads DOM scroll state from the real model shape.
- Public coordinate-taking tools accept both string and list `loc` values again.

## Live-Proven Behaviors

- Notepad typing passes with verified character-count and caret movement.
- Calculator result verification is possible from `Snapshot` state.
- Stale label state fails closed, then succeeds after fresh `Snapshot` and retry.
- Explorer navigation and download verification pass when checked through shell/filesystem state, not just window titles.
- Browser-to-desktop and Settings-to-desktop focus recovery both have passing cases after the switch fix.

## What To Trust

- For benchmark state: `research/test_matrix.md`
- For concrete patches: `research/patches.md`
- For exact run evidence: `research/results/*.md`
- For the next handoff: `research/next_session.md`

This file should stay short. Detailed chronology belongs in `results/*.md`, not here.
