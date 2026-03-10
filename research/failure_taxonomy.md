# Windows-MCP Failure Taxonomy

Date: 2026-03-10

## Buckets
| Bucket | Definition | Typical Evidence | First Response |
| --- | --- | --- | --- |
| MCP startup/init timeout | Server or tool host does not become usable promptly | first call stalls, startup log delay, transport init error | capture startup timing, trim init work, add self-check |
| tool invocation timeout | Individual tool exceeds timeout | timeout return, hung subprocess, no follow-up state change | record tool, parameters, elapsed time |
| wrong active window / focus stolen | Action lands in the wrong app/window | focused window title changes, text appears elsewhere | re-observe, switch/recover focus, add pre-action window guard |
| stale snapshot / state changed before action | Cached labels/coordinates no longer map to current UI | label exists in old snapshot but click misses | refresh state before action, add snapshot age check |
| wrong element selected | Locator matched the wrong control | focus moves to nearby/similar control | tighten locator, include window/app context |
| coordinate drift | Absolute point is valid but wrong on current frame | repeated off-target clicks near intended area | prefer semantic target, verify geometry after observe |
| DPI / scaling mismatch | Coordinates or bounds do not match rendered UI scale | consistent offset on scaled displays | normalize on virtual desktop space, log DPI |
| multi-monitor confusion | Action or crop occurs on wrong display | active window omitted, wrong screenshot region | pin display set, log display topology |
| app launch race | App process/window not ready when next action fires | launch acknowledged, no stable window yet | wait on observable window existence |
| window transition race | UI changes between action and follow-up step | dialog appears, view re-renders, action chain diverges | single-step loop, verify after every action |
| browser DOM mismatch vs desktop UI mismatch | Wrong automation layer chosen | browser UI clicked instead of page DOM, or DOM missing | route browser task to DOM/Playwright path |
| localization / non-English UI mismatch | Labels/app names differ from expected English strings | fuzzy match fails, labels translated | detect locale, avoid English-only assumptions |
| blocked by permissions / UAC / system dialog | Elevated or secure UI prevents control | window exists but actions fail silently | classify privileged boundary, avoid blind retries |
| clipboard failure | Clipboard busy, empty, or wrong format | Win32 clipboard open failure, stale content | retry with explicit verification |
| tool schema / contract mismatch | Exposed tool contract differs from what the live server actually accepts | validation error before action, wrapper docs and server schema disagree | capture failing payload, normalize wrapper/server parameter shapes |
| shell quoting/path issues | PowerShell or file paths fail due to escaping | command errors on quotes/spaces | capture exact command, centralize quoting |
| process attach / detach failure | foreground switching or thread attach fails | app switch says success but focus unchanged | verify target foreground handle after switch |
| hidden/minimized window issues | target exists but is not actionable | window listed as minimized/hidden | restore/show before acting |
| fragile prompt planning | too many assumptions before observe | long blind action chain, cascading failure | observe-first, one-action loop |
| insufficient post-action verification | system claims success without evidence | tool returns success string but UI unchanged | require re-observe and postcondition check |
| shell hot-reload boundary / stale shell code | shell-owned code changed but hot reload only refreshed the worker | reload generation increments but shell metadata/behavior stays old | mark shell restart required; reconnect host before judging shell changes |
| unrecoverable tool exception / server bug | internal exception or bad return path | traceback, malformed response, crash | isolate repro, patch code, add regression test |

## Observed Failures Log Format
Use this template for every failure:

```md
### Failure Entry
- Timestamp:
- Task:
- Exact step:
- Expected result:
- Actual result:
- Probable cause:
- Evidence:
- Confidence:
- Suggested fix:
- Bucket:
```

## Observed Failures
### Failure Entry
- Timestamp: 2026-03-10 11:25 CST
- Task: Open Notepad and type text
- Exact step: `App.launch(name=\"Notepad\")`
- Expected result: Notepad launches and becomes detectable.
- Actual result: Tool returned `Notepad not found in start menu.`
- Probable cause: Start Menu discovery is localized; the installed app name is `记事本`, not `Notepad`.
- Evidence: `PowerShell` query `Get-StartApps | Where-Object { $_.Name -match 'Notepad|记事本' }` returned `{\"Name\":\"记事本\",\"AppID\":\"Microsoft.WindowsNotepad_8wekyb3d8bbwe!App\"}`.
- Confidence: High
- Suggested fix: locale-aware app aliasing; fall back to AppID/package lookup when fuzzy English name search misses.
- Bucket: localization / non-English UI mismatch

### Failure Entry
- Timestamp: 2026-03-10 11:26 CST
- Task: Open Notepad and type text
- Exact step: `App.launch(name=\"记事本\")`
- Expected result: Tool reports a successful launch when Notepad becomes foreground.
- Actual result: Tool returned `Launching 记事本 sent, but window not detected yet.` even though `Snapshot` immediately showed `latest.log - Notepad` as the focused window.
- Probable cause: launch verification is checking the requested app name against the eventual window title; localized app name and actual title diverge.
- Evidence: post-action `Snapshot` showed focused window `latest.log - Notepad`.
- Confidence: High
- Suggested fix: separate process/window existence verification from exact title/name matching; record detected PID/handle and return structured status.
- Bucket: insufficient post-action verification

### Failure Entry
- Timestamp: 2026-03-10 11:26 CST
- Task: Open Notepad and type text
- Exact step: `Type(label=34, text=...)`
- Expected result: text is entered into the focused Notepad document.
- Actual result: Tool failed with `name '_INPUTUnion' is not defined`.
- Probable cause: `src/windows_mcp/uia/core.py` uses `_INPUTUnion` but only imports `from .enums import *`; star-import does not include underscore-prefixed names.
- Evidence: source inspection showed `_INPUTUnion` is defined in `src/windows_mcp/uia/enums.py` and referenced in `src/windows_mcp/uia/core.py` without explicit import.
- Confidence: High
- Suggested fix: explicitly import `_INPUTUnion` in `uia/core.py`; add a regression test around keyboard input construction.
- Bucket: unrecoverable tool exception / server bug

### Failure Entry
- Timestamp: 2026-03-10 11:28 CST
- Task: Switch between apps
- Exact step: `App.switch(name=\"Notepad\")`
- Expected result: Notepad becomes the focused window.
- Actual result: Tool returned `Switched to Latest.Log - Notepad window.` but the next `Snapshot` still showed Calculator as the focused window.
- Probable cause: foreground handoff failed or was immediately stolen back, and the tool returned success without verifying the final foreground handle.
- Evidence: post-action `Snapshot` focused window remained `Calculator`.
- Confidence: High
- Suggested fix: after switch, re-read the foreground handle/title and fail if it does not match target window within a short observable wait.
- Bucket: wrong active window / focus stolen

### Failure Entry
- Timestamp: 2026-03-10 11:27 CST
- Task: Open Calculator and compute something
- Exact step: verify arithmetic result after button clicks
- Expected result: the calculator result should be machine-verifiable.
- Actual result: `Snapshot` exposed interactive buttons but not the display/result text, so the arithmetic result was not directly verifiable through the current tool surface.
- Probable cause: state model omits informative text nodes from the `Snapshot` response.
- Evidence: multiple `Snapshot` responses showed buttons and focus metadata but no calculator display value.
- Confidence: Medium
- Suggested fix: expose informative/text nodes in `Snapshot`, or add an app-specific calculator result reader.
- Bucket: insufficient post-action verification

### Failure Entry
- Timestamp: 2026-03-10 11:40 CST
- Task: Switch between apps
- Exact step: local patched `switch_app("Notepad")` from Calculator foreground
- Expected result: Notepad becomes foreground if the switch API reports success.
- Actual result: `bring_window_to_top()` hit `AttachThreadInput` access denied, and `switch_app()` returned failure while Calculator remained foreground.
- Probable cause: thread-input attach is not always permitted across the current foreground and target processes on this machine.
- Evidence: local probe output showed `Failed to switch focus to 无标题 - Notepad window.` and the active window after re-observe was still `Calculator`.
- Confidence: High
- Suggested fix: add a fallback focus path using UIA `SetFocus()` plus Win32 foreground/topmost calls before concluding failure.
- Bucket: process attach / detach failure

### Failure Entry
- Timestamp: 2026-03-10 11:52 CST
- Task: Tool-layer Snapshot after freshness patch
- Exact step: local in-process MCP `Snapshot`
- Expected result: Snapshot returns normally with the new timestamp and informative text sections.
- Actual result: `Desktop.get_state()` raised `UnboundLocalError` because `captured_at_epoch` was set from `end_time` before `end_time` was assigned.
- Probable cause: freshness metadata patch used the end timestamp before it existed in the control flow.
- Evidence: local in-process MCP harness reproduced the failure immediately on `Snapshot`.
- Confidence: High
- Suggested fix: assign `captured_at_epoch` directly from a fresh `time()` call before constructing `DesktopState`.
- Bucket: unrecoverable tool exception / server bug

### Failure Entry
- Timestamp: 2026-03-10 13:53 CST
- Task: Verify hot-reloaded shell metadata after source patch
- Exact step: `DevServer(mode="reload")` followed by `DevServer(mode="health")`
- Expected result: the live health payload should expose the newly added source-fingerprint fields after reload.
- Actual result: the worker generation incremented, but the health payload stayed on the old schema and did not expose the new shell-side fields.
- Probable cause: hot reload replaces only the internal worker; shell-owned code paths, including `DevServer` health serialization, stay stale until the shell process itself restarts.
- Evidence: reload returned `active_generation: 2`, but the following `health` response still lacked `active_source_hash` or any shell-staleness fields.
- Confidence: High
- Suggested fix: expose an explicit `shell_restart_required` signal and document that shell-owned changes require a full server reconnect, not only `DevServer.reload`.
- Bucket: shell hot-reload boundary / stale shell code

### Failure Entry
- Timestamp: 2026-03-10 13:56 CST
- Task: Live stale-state validation
- Exact step: `Wait(duration=11)` before a delayed label-based click
- Expected result: the tool should pause for roughly 11 seconds, then return control.
- Actual result: the tool call hung until the outer host deadline and failed with `timed out awaiting tools/call after 120s`.
- Probable cause: hot mode's internal worker `streamable-http` path does not reliably return longer-running tool calls.
- Evidence: live `Wait(2)` and `Wait(4)` succeeded, while live `Wait(8)` and `Wait(11)` hung; local in-memory MCP `Wait(8)` succeeded; direct worker HTTP `Wait(8)` timed out under an explicit 20 second guard.
- Confidence: High
- Suggested fix: short-term, execute `Wait` locally in the hot shell instead of forwarding it to the worker; longer-term, investigate or replace the internal worker transport for long-running calls.
- Bucket: tool invocation timeout

### Failure Entry
- Timestamp: 2026-03-10 14:27 CST
- Task: Verify restarted live shell health reporting
- Exact step: `DevServer(mode="health", timeout_seconds=20)`
- Expected result: live shell should return health JSON promptly after the host reconnect and shell restart.
- Actual result: tool call timed out at the outer 120 second deadline, while other live tools kept working.
- Probable cause: host-facing `DevServer` integration path is broken or blocked specifically for this tool, despite the underlying shell code being healthy.
- Evidence: live `Clipboard`, `Snapshot`, and `Wait(8)` all succeeded; local `DevServer.health` against a shell built from the same source returned immediately with the expected source-hash fields.
- Confidence: Medium
- Suggested fix: inspect the host/tool wrapper path for `DevServer` separately from normal public tools; meanwhile use process inspection plus live behavior of other shell-owned tools as fallback evidence.
- Bucket: tool invocation timeout

### Failure Entry
- Timestamp: 2026-03-10 14:31 CST
- Task: Determine whether long-call forwarding defect is generic
- Exact step: live `PowerShell` with `Start-Sleep -Seconds 8; Write-Output 'wmcp-long-call-ok'`
- Expected result: forwarded worker call should return within the 20 second tool timeout.
- Actual result: tool call hung until the outer 120 second tools/call deadline.
- Probable cause: hot mode's internal worker `streamable-http` bridge does not reliably complete forwarded calls once runtime exceeds a few seconds.
- Evidence: live `PowerShell` with 8 second sleep failed; live `PowerShell` with 4 second sleep succeeded; earlier live `Wait(8+)` failures and direct local worker HTTP probe showed the same pattern.
- Confidence: High
- Suggested fix: treat this as a generic forwarded long-call transport defect and prioritize a persistent-session stdio worker bridge or another local IPC alternative.
- Bucket: tool invocation timeout

### Failure Entry
- Timestamp: 2026-03-10 14:47 CST
- Task: Verify live shell after persistent stdio bridge rollout
- Exact step: live `DevServer(mode="health", timeout_seconds=20)` after the transport fix was already validated through other live tools
- Expected result: prompt health JSON from the restarted live shell.
- Actual result: the tool still timed out at the outer 120 second deadline.
- Probable cause: host-facing `DevServer` wrapper/integration issue that is independent of the worker transport path.
- Evidence: live forwarded `PowerShell Start-Sleep 8` now succeeds, `Snapshot` succeeds, and `App.launch("Calculator")` succeeds, but `DevServer` alone still times out.
- Confidence: High
- Suggested fix: debug the host-side tool path or manifest/wrapper integration for `DevServer` separately from the Windows-MCP worker transport.
- Bucket: tool invocation timeout

### Failure Entry
- Timestamp: 2026-03-10 14:59 CST
- Task: Repeated live Notepad typing benchmark
- Exact step: first append attempt with `Type(loc="500,400", text="x")`
- Expected result: the documented coordinate form should be accepted and the tool should type into the Notepad document.
- Actual result: the live tool rejected the call before execution with `Input should be a valid list` for `loc`.
- Probable cause: the exposed `Type` contract seen by Codex documents `loc` as a string-like coordinate form, while the active live server schema requires a numeric list.
- Evidence: retrying immediately with `Type(loc=[500,400], text="x")` succeeded, and a fresh `Snapshot` verified Notepad changed from `13 个字符` to `14 个字符`.
- Confidence: High
- Suggested fix: normalize the documented tool surface and the server-side schema so coordinate-taking tools accept one canonical shape, or explicitly support both string and list forms.
- Bucket: tool schema / contract mismatch

### Failure Entry
- Timestamp: 2026-03-10 15:05 CST
- Task: Live `DevServer.health` diagnosis after shell-side trace rollout
- Exact step: `DevServer(mode="health", timeout_seconds=20)` followed by reading `research/runtime/devserver_trace.jsonl`
- Expected result: health should return promptly, or the trace should show the request never reached Windows-MCP.
- Actual result: the outer tool call still timed out, but the shell trace showed `dev_invoker_enter` and `dev_server_sync_enter` immediately, then `dev_server_sync_health_ok` and `dev_invoker_ok` only about 132 seconds later.
- Probable cause: the remaining stall is inside the shell-side health path after `dev_server_sync` entry, not in the host wrapper before Windows-MCP is invoked.
- Evidence: trace lines recorded from the live shell process with timestamps `1773126300.907` for entry and `1773126432.652` / `1773126432.653` for completion.
- Confidence: High
- Suggested fix: instrument `health_snapshot()` step timings directly and reduce lock scope around shell hash / dirty-state work so the next live trace can isolate the exact delayed substep.
- Bucket: tool invocation timeout

### Failure Entry
- Timestamp: 2026-03-10 15:44 CST
- Task: Browser DOM extraction benchmark
- Exact step: `Scrape(url="https://example.com/", use_dom=true)`
- Expected result: DOM-backed scrape should return the page content for the active Chrome tab.
- Actual result: the tool failed with `'ScrollElementNode' object has no attribute 'vertical_scroll_percent'`.
- Probable cause: `scrape_handler()` was reading `vertical_scroll_percent` as an attribute on `tree_state.dom_node`, but the browser DOM root is stored as a `ScrollElementNode` whose scroll percentage lives in `metadata`.
- Evidence: source inspection showed `ScrollElementNode` has no `vertical_scroll_percent` field, while `tree/service.py` stores that value in `metadata`; patching `scrape_handler()` to read `dom_node.metadata["vertical_scroll_percent"]` fixed the live retry.
- Confidence: High
- Suggested fix: keep browser DOM handlers aligned with the actual `TreeState` model and add regression tests for DOM scrape paths.
- Bucket: unrecoverable tool exception / server bug

### Failure Entry
- Timestamp: 2026-03-10 15:50 CST
- Task: Settings dialog navigation
- Exact step: click-driven navigation from `Display` toward `Bluetooth & devices` using the fresh snapshot label for the sidebar target
- Expected result: the Settings window should remain foreground and navigate to the `Bluetooth & devices` page
- Actual result: the next snapshot showed focus on `Ubuntu`, and `Settings` was no longer the active foreground window
- Probable cause: Settings sidebar navigation is a fragile UI-transition path on this machine; the click likely raced a window transition or activated the wrong desktop/window target despite a fresh observe step
- Evidence: `ms-settings:display` launch verified correctly, the labeled click was issued from a fresh snapshot, and the immediate re-observe showed foreground focus stolen away from `Settings`; a direct `Start-Process 'ms-settings:bluetooth'` retry succeeded and landed on the expected page
- Confidence: Medium
- Suggested fix: route Settings tasks through protocol URIs / deep links when available instead of click-driven in-app navigation; reserve generic clicks for cases without a stable URI
- Bucket: window transition race

### Failure Entry
- Timestamp: 2026-03-10 15:57 CST
- Task: Recover from mistaken click or wrong focus
- Exact step: after intentionally switching foreground from `Settings` to Chrome, call `App.switch(name="Notepad")`
- Expected result: Notepad becomes the focused window so recovery can continue semantically
- Actual result: the tool returned `Failed to switch focus to *Wmcp-Type-Probe-1773117083.Txt - Notepad window.` and the next snapshot still showed Chrome as foreground
- Probable cause: the current switch path still loses some browser-to-desktop focus recoveries, likely in the foreground handoff / attach path rather than in name matching
- Evidence: pre-action snapshot showed Chrome foreground and a visible Notepad taskbar button; `App.switch` failed closed; a one-step taskbar click on the same Notepad target immediately recovered focus and allowed verified typing
- Confidence: Medium
- Suggested fix: treat `App.switch` as one recovery option, not the only one; consider a supervisor fallback to taskbar/window-button activation when the target is already visible in the latest snapshot
- Bucket: process attach / detach failure

### Failure Entry
- Timestamp: 2026-03-10 16:18 CST
- Task: Dynamic shell diagnostics before restart
- Exact step: first live attempt to call `DevServer(mode="call", name="describe_shell_file_delta", load_latest=true)` on a stale shell
- Expected result: the dynamic module should load from disk and report changed shell-owned files without requiring another shell restart
- Actual result: the first attempt failed because the dynamic module imported `get_shell_code_file_hashes` from the stale shell's `windows_mcp.dev_hot`, where that helper did not yet exist
- Probable cause: the dynamic diagnostic depended on newer shell-module symbols than the stale shell process had loaded
- Evidence: `list_calls` reported `dynamic_module_error: cannot import name 'get_shell_code_file_hashes' from 'windows_mcp.dev_hot'`
- Confidence: High
- Suggested fix: keep dynamic pre-restart diagnostics backward-compatible with the oldest shell that supports `mode="call"`, or provide explicit fallbacks instead of importing freshly added shell helpers
- Bucket: shell hot-reload boundary / stale shell code

### Failure Entry
- Timestamp: 2026-03-10 16:19 CST
- Task: Dynamic shell diagnostics before restart
- Exact step: second live attempt to call `describe_shell_file_delta` after removing the incompatible helper import
- Expected result: the dynamic module should report changed shell-owned files
- Actual result: the call still failed because the stale shell's `WorkerSupervisor` did not have `shell_loaded_file_hashes`
- Probable cause: the dynamic module assumed a newer supervisor schema than the live shell had loaded
- Evidence: live error `WorkerSupervisor object has no attribute 'shell_loaded_file_hashes'`
- Confidence: High
- Suggested fix: dynamic diagnostics should feature-detect supervisor attributes and fall back to a weaker but compatible method such as file `mtime` compared with `shell_started_at_epoch`
- Bucket: shell hot-reload boundary / stale shell code

### Failure Entry
- Timestamp: 2026-03-10 16:25 CST
- Task: Switch between apps
- Exact step: `App.switch(name="Notepad")` from Chrome foreground before the latest worker patch
- Expected result: Notepad becomes the foreground window, as Calculator already did from the same Chrome foreground
- Actual result: the tool returned failure and the next snapshot still showed Chrome foreground
- Probable cause: `bring_window_to_top()` completed the Win32 path without raising, but the actual foreground never changed and no fallback focus path ran
- Evidence: a detailed probe against the same target showed `AttachThreadInput` succeeded, `SetForegroundWindow` still did not move focus, and the final foreground handle remained Chrome; after patching a post-Win32 fallback and hot-reloading the worker, the same Chrome-to-Notepad switch passed live
- Confidence: High
- Suggested fix: if the target is still not foreground after the Win32 path completes, run the fallback focus path before concluding failure
- Bucket: process attach / detach failure

### Failure Entry
- Timestamp: 2026-03-10 16:34 CST
- Task: Verify restarted shell native call surface
- Exact step: after the shell was reported restarted, call live `DevServer(mode="call", name="describe_shell_file_delta", load_latest=false)`
- Expected result: the native call should exist on the restarted shell and report the new file-delta payload directly
- Actual result: the native shell still returned `Unknown DevServer call 'describe_shell_file_delta'. Available calls: describe_restart_boundary, list_calls`
- Probable cause: the connected MCP session remained attached to an older shell process or reattached to a shell that predated the latest native call-table patch
- Evidence: in the same live session, `describe_restart_boundary` reported `shell_loaded_source_hash = 7c72e5f8...`, `shell_current_source_hash = 1d8d6049...`, and `shell_restart_required = true`; `list_calls(load_latest=true)` also showed the new call only from the dynamic module, not native
- Confidence: High
- Suggested fix: expose explicit shell runtime identity in both native and dynamic diagnostics and compare PID/session/start-time before trusting a claimed restart; do not infer restart success from operator action alone
- Bucket: shell hot-reload boundary / stale shell code

### Failure Entry
- Timestamp: 2026-03-10 16:23 CST
- Task: Focus-diagnosis experiment
- Exact step: first comparison run of `switch_focus_diagnose.py` for Notepad and Calculator in parallel
- Expected result: two independent focus-diagnosis results that can be compared causally
- Actual result: the experiments interfered with each other because both manipulated the same shared desktop foreground state
- Probable cause: parallel execution was invalid for global UI-state experiments
- Evidence: the Notepad probe observed Calculator foreground during its measurement window because the Calculator probe ran concurrently and stole focus
- Confidence: High
- Suggested fix: never parallelize UI-state-changing experiments against the same desktop session; run them sequentially with fresh observes between runs
- Bucket: fragile prompt planning
