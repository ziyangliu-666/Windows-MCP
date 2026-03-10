# Patch List

## Planned
- Turn focus recovery into a repeatable benchmark instead of a set of one-off passes.
- Run `Background app launch then attach` from `research/test_matrix.md`.
- Run `Retry after UI change` from `research/test_matrix.md`.
- Decide whether a supervisor-level taskbar/window fallback is still needed after the switch fix.
- Keep shell diagnostics in maintenance mode unless they block a live benchmark.

## Implemented
- 2026-03-10: fixed keyboard input construction by explicitly importing `_INPUTUnion` in `src/windows_mcp/uia/core.py`.
- 2026-03-10: hardened `App.launch` so success is verified from observed process/window state instead of brittle title matching.
- 2026-03-10: hardened `App.switch` and `bring_window_to_top()` so focus changes are verified and fallback focus paths run before success is reported.
- 2026-03-10: expanded `Snapshot` and `DesktopState` to expose informative text and capture timestamps, and reject stale label-based actions after 10 seconds.
- 2026-03-10: restored `Desktop._ps_quote()` compatibility so the full test suite stayed green after the freshness work.
- 2026-03-10: switched hot mode to a persistent stdio worker bridge, and kept `Wait` shell-local, so long forwarded calls no longer hang on the old HTTP transport.
- 2026-03-10: trimmed `DevServer.health` fast-path work and added shell/runtime identity plus restart-boundary diagnostics for stale-shell debugging.
- 2026-03-10: fixed browser DOM scraping to read scroll metadata from the real model shape.
- 2026-03-10: normalized public coordinate tools so `Click`, `Type`, `Move`, and `Scroll` accept both string and list `loc` forms.
- 2026-03-10: added protocol-target launch support for Settings-style `ms-settings:*` deep links.
- 2026-03-10: added focused local probes and regression coverage for typing, focus switching, transport behavior, calculator verification, and in-process MCP validation.
