# Benchmark Test Matrix

Date: 2026-03-10

## Protocol
- Always follow: observe -> identify active window -> choose one action -> execute -> re-observe -> verify.
- Max blind action chain: 1.
- If the same failure repeats 3 times, stop brute force and classify it.
- Evidence to capture:
  - active window before action
  - exact action issued
  - active window after action
  - postcondition result
  - failure bucket if applicable

## Simple Tasks
| Task | Preconditions | Expected Postconditions | Rollback / Cleanup | Max Retries | Evidence |
| --- | --- | --- | --- | --- | --- |
| Open Notepad and type text | Notepad available in Start Menu | Notepad focused and expected text visible | close Notepad without saving if created | 2 | snapshots before/after typing |
| Open Calculator and compute | Calculator available | calculator focused and result matches | close calculator | 2 | snapshots with buttons/result |
| Clipboard copy/paste | clipboard tools available, text field available if paste path used | clipboard contains expected text; pasted text matches if UI tested | restore clipboard with neutral text if needed | 2 | clipboard reads before/after |
| Create folder and rename it | writable desktop path | folder exists with new name | remove test folder | 2 | filesystem info/list output |
| Open Explorer to known path | File Explorer available | explorer focused at target path | close window | 2 | snapshot window title + path clues |

## Medium Tasks
| Task | Preconditions | Expected Postconditions | Rollback / Cleanup | Max Retries | Evidence |
| --- | --- | --- | --- | --- | --- |
| Switch between two apps | two windows open | target app becomes foreground | close opened apps | 2 | snapshots before/after switch |
| Resize or move a window | normal, non-maximized window | bounding box changes as requested | restore original size if easy | 2 | snapshot window geometry |
| Run terminal command and verify output | terminal or PowerShell available | command output matches expectation | none | 2 | command output |
| Fill multiple input boxes | app with at least two editable fields | each field contains expected text | clear test fields | 2 | before/after snapshots |
| Multi-step file ops | temp directory available | copied/moved files in expected locations | delete temp artifacts | 2 | filesystem output |
| Browser search and extract | browser installed/network available | search results visible and content extracted | close tab/window | 2 | DOM snapshot or scrape output |

## Hard Tasks
| Task | Preconditions | Expected Postconditions | Rollback / Cleanup | Max Retries | Evidence |
| --- | --- | --- | --- | --- | --- |
| Mixed browser + desktop workflow | browser plus native app available | browser and desktop handoff succeeds | delete downloaded/test artifacts | 1 | DOM + desktop snapshots |
| Download file and verify in Explorer | browser download path known | file exists and Explorer shows it | delete downloaded file | 1 | browser/Explorer evidence |
| Settings dialog navigation | Settings app available | target settings page reached | close Settings | 1 | snapshot page labels |
| Retry after UI change | app with predictable re-render | recovery reaches target after one failed locator | restore initial state | 1 | paired failure/success evidence |
| Background app launch then attach | launchable app with delayed window | attach succeeds after later observe | close app | 1 | process + snapshot evidence |
| Recover from mistaken click or wrong focus | intentionally induce focus change | supervisor recovers and completes task | close test apps | 1 | failure classification + recovery log |

## Latest Results
| Task | Latest Environment | Result | Notes |
| --- | --- | --- | --- |
| Open Notepad and type text | Live MCP hot worker | Pass | Repeated live append checks advanced Notepad from `13` to `16` characters with verified caret movement. The string-coordinate `Type.loc` fix is now also verified live. |
| Coordinate tool string `loc` contract | Live MCP hot shell | Pass | `Type`, `Click`, `Move`, and `Scroll` all passed live with string `loc` values after the wrapper/server contract fix. |
| Open Calculator and compute | Local harness | Pass | Calculator display verification works through native informative text. |
| Clipboard copy/paste | Live MCP | Pass | `Clipboard` remained deterministic across repeated checks. |
| Create folder and rename it | Live MCP | Pass | `FileSystem` workflow stayed deterministic and easy to verify. |
| Switch between two apps | Live MCP hot worker | Pass | Controlled live sequence verified `App.switch("Notepad")` and reverse `App.switch("Calculator")` with fresh snapshots, so the earlier miss is now classified as focus-environmental. |
| Stale snapshot protection | Live MCP hot worker | Pass | Label-based action refused to execute after cached state aged out, and a fresh observe plus retry then succeeded in focusing `Settings` from the taskbar. |
| Wait tool under hot mode | Live MCP hot shell | Pass after shell restart | `Wait(8)` now succeeds because the shell-local bypass is active. |
| PowerShell long-running command | Live MCP hot shell with persistent stdio | Pass | `Start-Sleep 8` now returns live, confirming the worker transport defect is fixed in the connected session. |
| DevServer health | Live MCP hot shell | Pass | After removing git-based dirty detection from the health fast path, `DevServer.health` now returns promptly with `shell_source_dirty = null`, `shell_source_dirty_check = "disabled"`, and `active_worker_transport = "stdio-persistent"`. |
| DevServer reload | Live MCP hot shell | Pass | Live `DevServer.reload` returned promptly, advanced `active_generation` from `1` to `2`, and `health` immediately reflected `reload_count = 1` with `last_reload_status = "reloaded:tool"`. |
| Explorer navigation to known path | Live MCP with shell-assisted launch | Pass | `explorer.exe` opened `C:\\Users\\Rufus\\src\\Windows-MCP\\research`; snapshot focused the correct Explorer window and Shell COM enumeration reported the target path. |
| Terminal command verification | Live MCP shell path | Pass | `PowerShell` returned `results_count=17` and `cwd=C:\\Users\\Rufus\\src\\Windows-MCP`, matching the expected repo state. |
| Browser DOM extract | Live MCP worker | Pass after patch | `Scrape(use_dom=true)` initially crashed on a `ScrollElementNode` attribute bug; after a hot worker reload it returned `Example Domain` content from the active Chrome tab. |
| Mixed browser + desktop handoff | Live MCP hot shell + worker | Pass with recovery | DOM extraction from Chrome was followed by a failed `App.switch("Notepad")`, a taskbar-click recovery, and a verified Notepad append from `16` to `30` characters using the browser-derived title `Example Domain`. |
| Settings navigation via sidebar click | Live MCP hot shell | Fail | Launching `ms-settings:display` succeeded, but click-driven navigation toward `Bluetooth & devices` lost the `Settings` foreground and the next snapshot showed focus on `Ubuntu`. |
| Settings navigation via protocol URI | Live MCP hot shell | Pass | `Start-Process 'ms-settings:bluetooth'` focused `Settings` directly on the expected `Bluetooth & devices` page. |
| App.launch protocol target support | Local in-process MCP | Pass | `App(mode="launch", name="ms-settings:bluetooth")` returned `Settings launched.`, and a follow-up local `Snapshot` verified the focused window was `Settings` on the `Bluetooth & devices` page. |
| Recover from wrong focus | Live MCP hot shell | Pass with recovery | After intentionally switching to Chrome, `App.switch("Notepad")` failed closed, but a taskbar-button fallback recovered Notepad and typing advanced the file from `30` to `33` characters. |
| DevServer dynamic shell delta on stale shell | Live MCP hot shell | Pass after compatibility fixes | Native `describe_restart_boundary` was stale, but dynamic `describe_shell_file_delta` loaded from disk and reported changed shell files with `fallback_mode = "mtime_since_shell_start"` without another restart. |
| App.launch protocol target support | Live MCP hot shell | Pass | `App(mode="launch", name="ms-settings:bluetooth")` returned `Settings launched.`, and the next `Snapshot` verified foreground `Settings` on `Bluetooth & devices`. |
| App.switch from Chrome to Calculator | Live MCP hot shell | Pass | From Chrome foreground, `App.switch("Calculator")` succeeded and a fresh snapshot verified Calculator foreground. |
| App.switch from Chrome to Notepad | Live MCP hot worker | Pass after patch | Before patch it failed while Chrome stayed foreground; after adding a post-Win32 fallback in `bring_window_to_top()` and hot-reloading the worker, the same Chrome-to-Notepad switch passed and Snapshot verified Notepad foreground. |
| Download file and verify in Explorer | Live MCP hot shell plus browser | Pass | The benchmark file appeared in `C:\\Users\\Rufus\\Downloads`, file contents matched `windows-mcp download benchmark`, and `explorer.exe /select,...` focused Explorer with the file selected. Chrome still showed a localhost error page, so filesystem-plus-Explorer verification was stronger than browser-title verification. |
| Dynamic pre-restart shell identity | Live MCP hot shell with stale native calls | Pass | `DevServer(mode="call", name="describe_shell_file_delta", load_latest=true)` now returns shell PID, synthesized session ID, start time, loaded/current source hashes, and `shell_restart_required = true` even when the native shell call table is still stale. |
| App.switch from Settings to Notepad | Live MCP hot worker | Pass after patch | With `Settings` foreground on `Bluetooth & devices`, `App.switch("Notepad")` moved focus to Notepad, and a follow-up `Type(loc="500,400", text="R")` increased the file from `33` to `34` characters. |
