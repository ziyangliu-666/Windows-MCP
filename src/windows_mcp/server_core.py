from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any, Awaitable, Callable, Literal

from fastmcp import Client, Context, FastMCP
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations

from windows_mcp import filesystem
from windows_mcp.analytics import PostHogAnalytics
from windows_mcp.desktop.service import Desktop, Size
from windows_mcp.watchdog.service import WatchDog


logger = logging.getLogger(__name__)

MAX_IMAGE_WIDTH = 1920
MAX_IMAGE_HEIGHT = 1080
MAX_LABEL_STATE_AGE_SECONDS = 10.0

INSTRUCTIONS = dedent(
    """
    Windows MCP server provides tools to interact directly with the Windows desktop,
    thus enabling to operate the desktop on the user's behalf.
    """
).strip()


@dataclass
class LocalRuntime:
    desktop: Desktop
    watchdog: WatchDog | None
    analytics: PostHogAnalytics | None
    screen_size: Size | None
    role: str = "local"
    generation: int = 0
    started_at_epoch: float = field(default_factory=time.time)


async def create_local_runtime(role: str = "local", generation: int = 0) -> LocalRuntime:
    analytics = None
    if os.getenv("ANONYMIZED_TELEMETRY", "true").lower() != "false":
        analytics = PostHogAnalytics()

    desktop = Desktop()
    watchdog = WatchDog()
    screen_size = desktop.get_screen_size()
    watchdog.set_focus_callback(desktop.tree.on_focus_change)
    watchdog.start()
    await asyncio.sleep(1)

    return LocalRuntime(
        desktop=desktop,
        watchdog=watchdog,
        analytics=analytics,
        screen_size=screen_size,
        role=role,
        generation=generation,
    )


async def close_local_runtime(runtime: LocalRuntime | None) -> None:
    if runtime is None:
        return
    if runtime.watchdog:
        runtime.watchdog.stop()
    if runtime.analytics:
        await runtime.analytics.close()


def _collect_client_data(ctx: Context | None) -> dict[str, Any]:
    if not ctx:
        return {}
    try:
        if (
            ctx.session
            and ctx.session.client_params
            and ctx.session.client_params.clientInfo
        ):
            info = ctx.session.client_params.clientInfo
            return {"client_name": info.name, "client_version": info.version}
    except Exception:
        pass
    return {}


async def invoke_with_analytics(
    runtime: LocalRuntime,
    analytics_name: str,
    func,
    *,
    ctx: Context | None = None,
    **kwargs,
):
    start = time.time()
    client_data = _collect_client_data(ctx)
    try:
        result = await asyncio.to_thread(func, runtime, **kwargs)
        duration_ms = int((time.time() - start) * 1000)
        if runtime.analytics:
            await runtime.analytics.track_tool(
                analytics_name,
                {"duration_ms": duration_ms, "success": True, **client_data},
            )
        return result
    except Exception as error:
        duration_ms = int((time.time() - start) * 1000)
        if runtime.analytics:
            await runtime.analytics.track_error(
                error,
                {"tool_name": analytics_name, "duration_ms": duration_ms, **client_data},
            )
        raise


def app_handler(
    runtime: LocalRuntime,
    *,
    mode: str = "launch",
    name: str | None = None,
    window_loc: list[int] | None = None,
    window_size: list[int] | None = None,
):
    return runtime.desktop.app(mode, name, window_loc, window_size)


def powershell_handler(runtime: LocalRuntime, *, command: str, timeout: int = 30):
    try:
        response, status_code = runtime.desktop.execute_command(command, timeout)
        return f"Response: {response}\nStatus Code: {status_code}"
    except Exception as e:
        return f"Error executing command: {str(e)}\nStatus Code: 1"


def filesystem_handler(
    runtime: LocalRuntime,
    *,
    mode: str,
    path: str,
    destination: str | None = None,
    content: str | None = None,
    pattern: str | None = None,
    recursive: bool | str = False,
    append: bool | str = False,
    overwrite: bool | str = False,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
    show_hidden: bool | str = False,
) -> str:
    try:
        from platformdirs import user_desktop_dir

        default_dir = user_desktop_dir()
        if not os.path.isabs(path):
            path = os.path.join(default_dir, path)
        if destination and not os.path.isabs(destination):
            destination = os.path.join(default_dir, destination)

        recursive = recursive is True or (
            isinstance(recursive, str) and recursive.lower() == "true"
        )
        append = append is True or (isinstance(append, str) and append.lower() == "true")
        overwrite = overwrite is True or (
            isinstance(overwrite, str) and overwrite.lower() == "true"
        )
        show_hidden = show_hidden is True or (
            isinstance(show_hidden, str) and show_hidden.lower() == "true"
        )

        match mode:
            case "read":
                return filesystem.read_file(path, offset=offset, limit=limit, encoding=encoding)
            case "write":
                if content is None:
                    return "Error: content parameter is required for write mode."
                return filesystem.write_file(path, content, append=append, encoding=encoding)
            case "copy":
                if destination is None:
                    return "Error: destination parameter is required for copy mode."
                return filesystem.copy_path(path, destination, overwrite=overwrite)
            case "move":
                if destination is None:
                    return "Error: destination parameter is required for move mode."
                return filesystem.move_path(path, destination, overwrite=overwrite)
            case "delete":
                return filesystem.delete_path(path, recursive=recursive)
            case "list":
                return filesystem.list_directory(
                    path, pattern=pattern, recursive=recursive, show_hidden=show_hidden
                )
            case "search":
                if pattern is None:
                    return "Error: pattern parameter is required for search mode."
                return filesystem.search_files(path, pattern, recursive=recursive)
            case "info":
                return filesystem.get_file_info(path)
            case _:
                return (
                    f'Error: Unknown mode "{mode}". Use: read, write, copy, move, '
                    "delete, list, search, info."
                )
    except Exception as e:
        return f"Error in File tool: {str(e)}"


def snapshot_handler(
    runtime: LocalRuntime,
    *,
    use_vision: bool | str = False,
    use_dom: bool | str = False,
    width_reference_line: int | None = None,
    height_reference_line: int | None = None,
    display: list[int] | None = None,
):
    desktop = runtime.desktop
    try:
        use_vision = use_vision is True or (
            isinstance(use_vision, str) and use_vision.lower() == "true"
        )
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
        display_indices = desktop.parse_display_selection(display)

        grid_lines = None
        if width_reference_line and height_reference_line:
            grid_lines = (int(width_reference_line), int(height_reference_line))

        desktop_state = desktop.get_state(
            use_vision=use_vision,
            use_dom=use_dom,
            as_bytes=False,
            grid_lines=grid_lines,
            display_indices=display_indices,
            max_image_size=Size(width=MAX_IMAGE_WIDTH, height=MAX_IMAGE_HEIGHT),
        )

        interactive_elements = desktop_state.tree_state.interactive_elements_to_string()
        scrollable_elements = desktop_state.tree_state.scrollable_elements_to_string()
        informative_elements = desktop_state.tree_state.informative_elements_to_string()
        windows = desktop_state.windows_to_string()
        active_window = desktop_state.active_window_to_string()
        active_desktop = desktop_state.active_desktop_to_string()
        all_desktops = desktop_state.desktops_to_string()

        screenshot_bytes = None
        if use_vision and desktop_state.screenshot is not None:
            buffered = io.BytesIO()
            desktop_state.screenshot.save(buffered, format="PNG")
            screenshot_bytes = buffered.getvalue()
            buffered.close()
    except Exception as e:
        logger.warning(
            "Snapshot failed with display=%s use_vision=%s use_dom=%s",
            display,
            use_vision if "use_vision" in locals() else None,
            use_dom if "use_dom" in locals() else None,
            exc_info=True,
        )
        return [f"Error capturing desktop state: {str(e)}. Please try again."]

    metadata_text = f"Cursor Position: {desktop_state.cursor_position}\n"
    if desktop_state.captured_at_epoch is not None:
        metadata_text += f"Snapshot Timestamp: {desktop_state.captured_at_epoch:.3f}\n"
        metadata_text += "Snapshot Coordinate Source: Current desktop capture\n"
    if desktop_state.screenshot_size:
        metadata_text += f"Screenshot Resolution: {desktop_state.screenshot_size.to_string()}\n"
    if desktop_state.screenshot_region:
        metadata_text += f"Screenshot Region: {desktop_state.screenshot_region.xyxy_to_string()}\n"
    if desktop_state.screenshot_displays:
        metadata_text += (
            f"Displays: {','.join(str(index) for index in desktop_state.screenshot_displays)}\n"
        )
        metadata_text += "Coordinate Space: Virtual desktop coordinates\n"

    return [dedent(
        f"""
        {metadata_text}
        Active Desktop:
        {active_desktop}

        All Desktops:
        {all_desktops}

        Focused Window:
        {active_window}

        Opened Windows:
        {windows}

        List of Interactive Elements:
        {interactive_elements or "No interactive elements found."}

        List of Informative Text Elements:
        {informative_elements or "No informative text elements found."}

        List of Scrollable Elements:
        {scrollable_elements or "No scrollable elements found."}
        """
    )] + ([Image(data=screenshot_bytes, format="png")] if use_vision and screenshot_bytes else [])


def _resolve_label(runtime: LocalRuntime, label: int) -> list[int]:
    runtime.desktop.require_fresh_desktop_state(MAX_LABEL_STATE_AGE_SECONDS)
    try:
        return list(runtime.desktop.get_coordinates_from_label(label))
    except Exception as e:
        raise ValueError(f"Failed to find element with label {label}: {e}") from e


def _normalize_loc_argument(loc: list[int] | str | None) -> list[int] | None:
    if loc is None:
        return None
    if isinstance(loc, list):
        return loc
    value = loc.strip()
    if not value:
        raise ValueError("Location cannot be empty.")
    if value[0] in "([" and value[-1] in ")]":
        value = value[1:-1].strip()
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError("Location string must be in the form 'x,y'.")
    try:
        return [int(parts[0]), int(parts[1])]
    except ValueError as exc:
        raise ValueError("Location string must contain exactly two integers.") from exc


def click_handler(
    runtime: LocalRuntime,
    *,
    loc: list[int] | str | None = None,
    label: int | None = None,
    button: str = "left",
    clicks: int = 1,
) -> str:
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    loc = _normalize_loc_argument(loc)
    if label is not None:
        loc = _resolve_label(runtime, label)
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    x, y = loc[0], loc[1]
    runtime.desktop.click(loc=loc, button=button, clicks=clicks)
    num_clicks = {0: "Hover", 1: "Single", 2: "Double"}
    return f"{num_clicks.get(clicks)} {button} clicked at ({x},{y})."


def type_handler(
    runtime: LocalRuntime,
    *,
    text: str,
    loc: list[int] | str | None = None,
    label: int | None = None,
    clear: bool | str = False,
    caret_position: str = "idle",
    press_enter: bool | str = False,
) -> str:
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    loc = _normalize_loc_argument(loc)
    if label is not None:
        loc = _resolve_label(runtime, label)
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    x, y = loc[0], loc[1]
    runtime.desktop.type(
        loc=loc,
        text=text,
        caret_position=caret_position,
        clear=clear,
        press_enter=press_enter,
    )
    return f"Typed {text} at ({x},{y})."


def scroll_handler(
    runtime: LocalRuntime,
    *,
    loc: list[int] | str | None = None,
    label: int | None = None,
    type: str = "vertical",
    direction: str = "down",
    wheel_times: int = 1,
) -> str:
    loc = _normalize_loc_argument(loc)
    if label is not None:
        loc = _resolve_label(runtime, label)
    if loc and len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    response = runtime.desktop.scroll(loc, type, direction, wheel_times)
    if response:
        return response
    return (
        f"Scrolled {type} {direction} by {wheel_times} wheel times at ({loc[0]},{loc[1]})."
        if loc
        else ""
    )


def move_handler(
    runtime: LocalRuntime,
    *,
    loc: list[int] | str | None = None,
    label: int | None = None,
    drag: bool | str = False,
) -> str:
    drag = drag is True or (isinstance(drag, str) and drag.lower() == "true")
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    loc = _normalize_loc_argument(loc)
    if label is not None:
        loc = _resolve_label(runtime, label)
    if len(loc) != 2:
        raise ValueError("loc must be a list of exactly 2 integers [x, y]")
    x, y = loc[0], loc[1]
    if drag:
        runtime.desktop.drag(loc)
        return f"Dragged to ({x},{y})."
    runtime.desktop.move(loc)
    return f"Moved the mouse pointer to ({x},{y})."


def shortcut_handler(runtime: LocalRuntime, *, shortcut: str):
    runtime.desktop.shortcut(shortcut)
    return f"Pressed {shortcut}."


def wait_handler(runtime: LocalRuntime, *, duration: int) -> str:
    time.sleep(duration)
    return f"Waited for {duration} seconds."


def scrape_handler(runtime: LocalRuntime, *, url: str, use_dom: bool | str = False) -> str:
    use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
    if not use_dom:
        content = runtime.desktop.scrape(url)
        return f"URL:{url}\nContent:\n{content}"

    desktop_state = runtime.desktop.get_state(use_vision=False, use_dom=use_dom)
    tree_state = desktop_state.tree_state
    if not tree_state.dom_node:
        return f"No DOM information found. Please open {url} in browser first."
    dom_node = tree_state.dom_node
    vertical_scroll_percent = float(dom_node.metadata.get("vertical_scroll_percent", 0))
    content = "\n".join([node.text for node in tree_state.dom_informative_nodes])
    header_status = "Reached top" if vertical_scroll_percent <= 0 else "Scroll up to see more"
    footer_status = "Reached bottom" if vertical_scroll_percent >= 100 else "Scroll down to see more"
    return f"URL:{url}\nContent:\n{header_status}\n{content}\n{footer_status}"


def multi_select_handler(
    runtime: LocalRuntime,
    *,
    locs: list[list[int]] | None = None,
    labels: list[int] | None = None,
    press_ctrl: bool | str = True,
) -> str:
    if locs is None and labels is None:
        raise ValueError("Either locs or labels must be provided.")
    locs = locs or []
    if labels is not None:
        runtime.desktop.require_fresh_desktop_state(MAX_LABEL_STATE_AGE_SECONDS)
        for label in labels:
            try:
                locs.append(list(runtime.desktop.get_coordinates_from_label(label)))
            except Exception as e:
                raise ValueError(f"Failed to find element with label {label}: {e}") from e

    press_ctrl = press_ctrl is True or (
        isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
    )
    runtime.desktop.multi_select(press_ctrl, locs)
    elements_str = "\n".join([f"({loc[0]},{loc[1]})" for loc in locs])
    return f"Multi-selected elements at:\n{elements_str}"


def multi_edit_handler(
    runtime: LocalRuntime,
    *,
    locs: list[list] | None = None,
    labels: list[list] | None = None,
) -> str:
    if locs is None and labels is None:
        raise ValueError("Either locs or labels must be provided.")
    locs = locs or []
    if labels is not None:
        runtime.desktop.require_fresh_desktop_state(MAX_LABEL_STATE_AGE_SECONDS)
        for item in labels:
            if len(item) != 2:
                raise ValueError(f"Each label item must be [label, text]. Invalid: {item}")
            try:
                label, text = int(item[0]), item[1]
                loc = list(runtime.desktop.get_coordinates_from_label(label))
                locs.append([loc[0], loc[1], text])
            except Exception as e:
                raise ValueError(f"Failed to process label item {item}: {e}") from e

    runtime.desktop.multi_edit(locs)
    elements_str = ", ".join([f"({e[0]},{e[1]}) with text '{e[2]}'" for e in locs])
    return f"Multi-edited elements at: {elements_str}"


def clipboard_handler(runtime: LocalRuntime, *, mode: str, text: str | None = None) -> str:
    try:
        import win32clipboard

        if mode == "get":
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    return f"Clipboard content:\n{data}"
                return "Clipboard is empty or contains non-text data."
            finally:
                win32clipboard.CloseClipboard()
        if mode == "set":
            if text is None:
                return 'Error: text parameter required for set mode.'
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                suffix = "..." if len(text) > 100 else ""
                return f"Clipboard set to: {text[:100]}{suffix}"
            finally:
                win32clipboard.CloseClipboard()
        return 'Error: mode must be either "get" or "set".'
    except Exception as e:
        return f"Error managing clipboard: {str(e)}"


def process_handler(
    runtime: LocalRuntime,
    *,
    mode: str,
    name: str | None = None,
    pid: int | None = None,
    sort_by: str = "memory",
    limit: int = 20,
    force: bool | str = False,
) -> str:
    try:
        if mode == "list":
            return runtime.desktop.list_processes(name=name, sort_by=sort_by, limit=limit)
        if mode == "kill":
            force = force is True or (isinstance(force, str) and force.lower() == "true")
            return runtime.desktop.kill_process(name=name, pid=pid, force=force)
        return 'Error: mode must be either "list" or "kill".'
    except Exception as e:
        return f"Error managing processes: {str(e)}"


def notification_handler(runtime: LocalRuntime, *, title: str, message: str) -> str:
    try:
        return runtime.desktop.send_notification(title, message)
    except Exception as e:
        return f"Error sending notification: {str(e)}"


def registry_handler(
    runtime: LocalRuntime,
    *,
    mode: str,
    path: str,
    name: str | None = None,
    value: str | None = None,
    type: str = "String",
) -> str:
    try:
        if mode == "get":
            if name is None:
                return "Error: name parameter is required for get mode."
            return runtime.desktop.registry_get(path=path, name=name)
        if mode == "set":
            if name is None:
                return "Error: name parameter is required for set mode."
            if value is None:
                return "Error: value parameter is required for set mode."
            return runtime.desktop.registry_set(path=path, name=name, value=value, reg_type=type)
        if mode == "delete":
            return runtime.desktop.registry_delete(path=path, name=name)
        if mode == "list":
            return runtime.desktop.registry_list(path=path)
        return 'Error: mode must be "get", "set", "delete", or "list".'
    except Exception as e:
        return f"Error accessing registry: {str(e)}"


@dataclass(frozen=True)
class HandlerSpec:
    analytics_name: str
    func: Any


HANDLERS: dict[str, HandlerSpec] = {
    "App": HandlerSpec("App-Tool", app_handler),
    "PowerShell": HandlerSpec("Powershell-Tool", powershell_handler),
    "FileSystem": HandlerSpec("FileSystem-Tool", filesystem_handler),
    "Snapshot": HandlerSpec("State-Tool", snapshot_handler),
    "Click": HandlerSpec("Click-Tool", click_handler),
    "Type": HandlerSpec("Type-Tool", type_handler),
    "Scroll": HandlerSpec("Scroll-Tool", scroll_handler),
    "Move": HandlerSpec("Move-Tool", move_handler),
    "Shortcut": HandlerSpec("Shortcut-Tool", shortcut_handler),
    "Wait": HandlerSpec("Wait-Tool", wait_handler),
    "Scrape": HandlerSpec("Scrape-Tool", scrape_handler),
    "MultiSelect": HandlerSpec("Multi-Select-Tool", multi_select_handler),
    "MultiEdit": HandlerSpec("Multi-Edit-Tool", multi_edit_handler),
    "Clipboard": HandlerSpec("Clipboard-Tool", clipboard_handler),
    "Process": HandlerSpec("Process-Tool", process_handler),
    "Notification": HandlerSpec("Notification-Tool", notification_handler),
    "Registry": HandlerSpec("Registry-Tool", registry_handler),
}


def build_local_invoker(runtime_provider: Callable[[], LocalRuntime]):
    async def invoker(tool_name: str, args: dict, ctx: Context | None):
        runtime = runtime_provider()
        handler = HANDLERS[tool_name]
        return await invoke_with_analytics(
            runtime,
            handler.analytics_name,
            handler.func,
            ctx=ctx,
            **args,
        )

    return invoker


ToolInvoker = Callable[[str, dict, Context | None], Awaitable[object]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    annotations: ToolAnnotations


PUBLIC_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="App",
        description=(
            "Manages Windows applications with three modes: 'launch' (opens the prescibed "
            "application), 'resize' (adjusts active window size/position), 'switch' "
            "(brings specific window into focus)."
        ),
        annotations=ToolAnnotations(title="App", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="PowerShell",
        description=(
            "A comprehensive system tool for executing any PowerShell commands. Use it to "
            "navigate the file system, manage files and processes, and execute system-level "
            "operations. Capable of accessing web content (e.g., via Invoke-WebRequest), "
            "interacting with network resources, and performing complex administrative tasks. "
            "This tool provides full access to the underlying operating system capabilities, "
            "making it the primary interface for system automation, scripting, and deep "
            "system interaction."
        ),
        annotations=ToolAnnotations(title="PowerShell", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    ),
    ToolSpec(
        name="FileSystem",
        description=(
            "Manages file system operations with eight modes: 'read' (read text file contents "
            "with optional line offset/limit), 'write' (create or overwrite a file, set "
            "append=True to append), 'copy' (copy file or directory to destination), 'move' "
            "(move or rename file/directory), 'delete' (delete file or directory, set "
            "recursive=True for non-empty dirs), 'list' (list directory contents with optional "
            "pattern filter), 'search' (find files matching a glob pattern), 'info' (get "
            "file/directory metadata like size, dates, type). Relative paths are resolved from "
            "the user's Desktop folder. Use absolute paths to access other locations."
        ),
        annotations=ToolAnnotations(title="FileSystem", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Snapshot",
        description=(
            "Captures complete desktop state including: system language, focused/opened windows, "
            "interactive elements (buttons, text fields, links, menus with coordinates), and "
            "scrollable areas. Set use_vision=True to include screenshot with cursor highlight. "
            "Set width_reference_lines/height_reference_lines to overlay a grid for better spatial "
            "reasoning (make sure vision is enabled to use it). Set use_dom=True for browser content "
            "to get web page elements instead of browser UI. Set display=[0] or display=[0,1] to "
            "limit all Snapshot information to specific screens; omit it to keep the default "
            "full-desktop behavior. Always call this first to understand the current desktop state "
            "before taking actions."
        ),
        annotations=ToolAnnotations(title="Snapshot", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    ),
    ToolSpec(
        name="Click",
        description=(
            "Performs mouse clicks at specified coordinates [x, y] or passing a UI element's "
            "label/id. Supports button types: 'left' for selection/activation, 'right' for "
            "context menus, 'middle'. Supports clicks: 0=hover only (no click), 1=single click "
            "(select/focus), 2=double click (open/activate). Provide either loc or label."
        ),
        annotations=ToolAnnotations(title="Click", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Type",
        description=(
            "Types text at specified coordinates [x, y] or passing a UI element's label/id. Set "
            "clear=True to clear existing text first, False to append. Set press_enter=True to submit "
            "after typing. Set caret_position to 'start' (beginning), 'end' (end), or 'idle' "
            "(default). Provide either loc or label."
        ),
        annotations=ToolAnnotations(title="Type", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Scroll",
        description=(
            "Scrolls at coordinates [x, y], a UI element's label/id, or current mouse position if "
            "loc=None. Type: vertical (default) or horizontal. Direction: up/down for vertical, "
            "left/right for horizontal. wheel_times controls amount (1 wheel ≈ 3-5 lines). Use for "
            "navigating long content, lists, and web pages."
        ),
        annotations=ToolAnnotations(title="Scroll", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    ),
    ToolSpec(
        name="Move",
        description=(
            "Moves mouse cursor to coordinates [x, y] or passing a UI element's label/id. Set "
            "drag=True to perform a drag-and-drop operation from the current mouse position to the "
            "target coordinates. Default (drag=False) is a simple cursor move (hover). Provide either "
            "loc or label."
        ),
        annotations=ToolAnnotations(title="Move", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    ),
    ToolSpec(
        name="Shortcut",
        description=(
            'Executes keyboard shortcuts using key combinations separated by +. Examples: "ctrl+c" '
            '(copy), "ctrl+v" (paste), "alt+tab" (switch apps), "win+r" (Run dialog), "win" '
            "(Start menu), \"ctrl+shift+esc\" (Task Manager). Use for quick actions and system commands."
        ),
        annotations=ToolAnnotations(title="Shortcut", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Wait",
        description=(
            "Pauses execution for specified duration in seconds. Use when waiting for: applications "
            "to launch/load, UI animations to complete, page content to render, dialogs to appear, "
            "or between rapid actions. Helps ensure UI is ready before next interaction."
        ),
        annotations=ToolAnnotations(title="Wait", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    ),
    ToolSpec(
        name="Scrape",
        description=(
            "Fetch content from a URL or the active browser tab. By default (use_dom=False), performs "
            "a lightweight HTTP request to the URL and returns markdown content of complete webpage. "
            "Note: Some websites may block automated HTTP requests. If this fails, open the page in a "
            "browser and retry with use_dom=True to extract visible text from the active tab's DOM "
            "within the viewport using the accessibility tree data."
        ),
        annotations=ToolAnnotations(title="Scrape", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    ),
    ToolSpec(
        name="MultiSelect",
        description=(
            "Selects multiple items such as files, folders, or checkboxes if press_ctrl=True, or "
            "performs multiple clicks if False. Pass locs (list of coordinates) or labels (list of "
            "UI element labels/ids)."
        ),
        annotations=ToolAnnotations(title="MultiSelect", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="MultiEdit",
        description=(
            "Enters text into multiple input fields at specified coordinates locs=[[x,y,text], ...] "
            "or using labels=[[label,text], ...]. Provide either locs or labels."
        ),
        annotations=ToolAnnotations(title="MultiEdit", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Clipboard",
        description='Manages Windows clipboard operations. Use mode="get" to read current clipboard content, mode="set" to set clipboard text.',
        annotations=ToolAnnotations(title="Clipboard", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    ),
    ToolSpec(
        name="Process",
        description='Manages system processes. Use mode="list" to list running processes with filtering and sorting options. Use mode="kill" to terminate processes by PID or name.',
        annotations=ToolAnnotations(title="Process", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Notification",
        description="Sends a Windows toast notification with a title and message. Useful for alerting the user remotely.",
        annotations=ToolAnnotations(title="Notification", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
    ToolSpec(
        name="Registry",
        description=(
            'Accesses the Windows Registry. Use mode="get" to read a value, mode="set" to create/update '
            'a value, mode="delete" to remove a value or key, mode="list" to list values and sub-keys '
            'under a path. Paths use PowerShell format (e.g. "HKCU:\\Software\\MyApp", '
            '"HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion").'
        ),
        annotations=ToolAnnotations(title="Registry", readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    ),
)

DEV_SERVER_SPEC = ToolSpec(
    name="DevServer",
    description=(
        "Development-only control surface for shell/worker hot reload. "
        'Use mode="health" to inspect runtime state, mode="reload" to force a worker reload, '
        'or mode="call" to invoke a named shell diagnostic.'
    ),
    annotations=ToolAnnotations(title="DevServer", readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)


def _spec(name: str) -> ToolSpec:
    for spec in PUBLIC_TOOL_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(name)


def register_public_tools(mcp: FastMCP, invoker: ToolInvoker) -> None:
    @mcp.tool(name="App", description=_spec("App").description, annotations=_spec("App").annotations)
    async def app_tool(
        mode: Literal["launch", "resize", "switch"] = "launch",
        name: str | None = None,
        window_loc: list[int] | None = None,
        window_size: list[int] | None = None,
        ctx: Context = None,
    ):
        return await invoker("App", {"mode": mode, "name": name, "window_loc": window_loc, "window_size": window_size}, ctx)

    @mcp.tool(name="PowerShell", description=_spec("PowerShell").description, annotations=_spec("PowerShell").annotations)
    async def powershell_tool(command: str, timeout: int = 30, ctx: Context = None):
        return await invoker("PowerShell", {"command": command, "timeout": timeout}, ctx)

    @mcp.tool(name="FileSystem", description=_spec("FileSystem").description, annotations=_spec("FileSystem").annotations)
    async def filesystem_tool(
        mode: Literal["read", "write", "copy", "move", "delete", "list", "search", "info"],
        path: str,
        destination: str | None = None,
        content: str | None = None,
        pattern: str | None = None,
        recursive: bool | str = False,
        append: bool | str = False,
        overwrite: bool | str = False,
        offset: int | None = None,
        limit: int | None = None,
        encoding: str = "utf-8",
        show_hidden: bool | str = False,
        ctx: Context = None,
    ):
        return await invoker(
            "FileSystem",
            {
                "mode": mode,
                "path": path,
                "destination": destination,
                "content": content,
                "pattern": pattern,
                "recursive": recursive,
                "append": append,
                "overwrite": overwrite,
                "offset": offset,
                "limit": limit,
                "encoding": encoding,
                "show_hidden": show_hidden,
            },
            ctx,
        )

    @mcp.tool(name="Snapshot", description=_spec("Snapshot").description, annotations=_spec("Snapshot").annotations)
    async def snapshot_tool(
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        ctx: Context = None,
    ):
        return await invoker(
            "Snapshot",
            {
                "use_vision": use_vision,
                "use_dom": use_dom,
                "width_reference_line": width_reference_line,
                "height_reference_line": height_reference_line,
                "display": display,
            },
            ctx,
        )

    @mcp.tool(name="Click", description=_spec("Click").description, annotations=_spec("Click").annotations)
    async def click_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        ctx: Context = None,
    ):
        return await invoker("Click", {"loc": loc, "label": label, "button": button, "clicks": clicks}, ctx)

    @mcp.tool(name="Type", description=_spec("Type").description, annotations=_spec("Type").annotations)
    async def type_tool(
        text: str,
        loc: list[int] | str | None = None,
        label: int | None = None,
        clear: bool | str = False,
        caret_position: Literal["start", "idle", "end"] = "idle",
        press_enter: bool | str = False,
        ctx: Context = None,
    ):
        return await invoker(
            "Type",
            {
                "text": text,
                "loc": loc,
                "label": label,
                "clear": clear,
                "caret_position": caret_position,
                "press_enter": press_enter,
            },
            ctx,
        )

    @mcp.tool(name="Scroll", description=_spec("Scroll").description, annotations=_spec("Scroll").annotations)
    async def scroll_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
        ctx: Context = None,
    ):
        return await invoker(
            "Scroll",
            {"loc": loc, "label": label, "type": type, "direction": direction, "wheel_times": wheel_times},
            ctx,
        )

    @mcp.tool(name="Move", description=_spec("Move").description, annotations=_spec("Move").annotations)
    async def move_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        drag: bool | str = False,
        ctx: Context = None,
    ):
        return await invoker("Move", {"loc": loc, "label": label, "drag": drag}, ctx)

    @mcp.tool(name="Shortcut", description=_spec("Shortcut").description, annotations=_spec("Shortcut").annotations)
    async def shortcut_tool(shortcut: str, ctx: Context = None):
        return await invoker("Shortcut", {"shortcut": shortcut}, ctx)

    @mcp.tool(name="Wait", description=_spec("Wait").description, annotations=_spec("Wait").annotations)
    async def wait_tool(duration: int, ctx: Context = None):
        return await invoker("Wait", {"duration": duration}, ctx)

    @mcp.tool(name="Scrape", description=_spec("Scrape").description, annotations=_spec("Scrape").annotations)
    async def scrape_tool(url: str, use_dom: bool | str = False, ctx: Context = None):
        return await invoker("Scrape", {"url": url, "use_dom": use_dom}, ctx)

    @mcp.tool(name="MultiSelect", description=_spec("MultiSelect").description, annotations=_spec("MultiSelect").annotations)
    async def multi_select_tool(
        locs: list[list[int]] | None = None,
        labels: list[int] | None = None,
        press_ctrl: bool | str = True,
        ctx: Context = None,
    ):
        return await invoker("MultiSelect", {"locs": locs, "labels": labels, "press_ctrl": press_ctrl}, ctx)

    @mcp.tool(name="MultiEdit", description=_spec("MultiEdit").description, annotations=_spec("MultiEdit").annotations)
    async def multi_edit_tool(
        locs: list[list] | None = None,
        labels: list[list] | None = None,
        ctx: Context = None,
    ):
        return await invoker("MultiEdit", {"locs": locs, "labels": labels}, ctx)

    @mcp.tool(name="Clipboard", description=_spec("Clipboard").description, annotations=_spec("Clipboard").annotations)
    async def clipboard_tool(mode: Literal["get", "set"], text: str | None = None, ctx: Context = None):
        return await invoker("Clipboard", {"mode": mode, "text": text}, ctx)

    @mcp.tool(name="Process", description=_spec("Process").description, annotations=_spec("Process").annotations)
    async def process_tool(
        mode: Literal["list", "kill"],
        name: str | None = None,
        pid: int | None = None,
        sort_by: Literal["memory", "cpu", "name"] = "memory",
        limit: int = 20,
        force: bool | str = False,
        ctx: Context = None,
    ):
        return await invoker(
            "Process",
            {"mode": mode, "name": name, "pid": pid, "sort_by": sort_by, "limit": limit, "force": force},
            ctx,
        )

    @mcp.tool(name="Notification", description=_spec("Notification").description, annotations=_spec("Notification").annotations)
    async def notification_tool(title: str, message: str, ctx: Context = None):
        return await invoker("Notification", {"title": title, "message": message}, ctx)

    @mcp.tool(name="Registry", description=_spec("Registry").description, annotations=_spec("Registry").annotations)
    async def registry_tool(
        mode: Literal["get", "set", "delete", "list"],
        path: str,
        name: str | None = None,
        value: str | None = None,
        type: Literal["String", "DWord", "QWord", "Binary", "MultiString", "ExpandString"] = "String",
        ctx: Context = None,
    ):
        return await invoker("Registry", {"mode": mode, "path": path, "name": name, "value": value, "type": type}, ctx)


def register_dev_server_tool(mcp: FastMCP, invoker: ToolInvoker) -> None:
    @mcp.tool(name=DEV_SERVER_SPEC.name, description=DEV_SERVER_SPEC.description, annotations=DEV_SERVER_SPEC.annotations)
    async def dev_server_tool(
        mode: str = "health",
        wait_for_ready: bool | str = True,
        timeout_seconds: int = 15,
        name: str | None = None,
        arguments_json: str = "{}",
        load_latest: bool | str = True,
        ctx: Context = None,
    ):
        return await invoker(
            DEV_SERVER_SPEC.name,
            {
                "mode": mode,
                "wait_for_ready": wait_for_ready,
                "timeout_seconds": timeout_seconds,
                "name": name,
                "arguments_json": arguments_json,
                "load_latest": load_latest,
            },
            ctx,
        )


def _normalize_model(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj


async def build_public_manifest_hash() -> str:
    temp_mcp = FastMCP(name="windows-mcp-manifest")

    async def _invoker(_tool_name: str, _args: dict, _ctx: Context | None):
        raise RuntimeError("Manifest server does not execute tools")

    register_public_tools(temp_mcp, _invoker)
    async with Client(temp_mcp) as client:
        tools = await client.list_tools()

    payload = [_normalize_model(tool) for tool in tools]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_public_tool_names() -> list[str]:
    return [spec.name for spec in PUBLIC_TOOL_SPECS]


def decode_image_content_data(data: str) -> bytes:
    return base64.b64decode(data)


def build_local_mcp() -> FastMCP:
    runtime_holder: dict[str, LocalRuntime] = {}

    @asynccontextmanager
    async def lifespan(_app: FastMCP):
        runtime_holder["runtime"] = await create_local_runtime(role="local", generation=0)
        try:
            yield
        finally:
            await close_local_runtime(runtime_holder.get("runtime"))

    server = FastMCP(name="windows-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)
    register_public_tools(server, build_local_invoker(lambda: runtime_holder["runtime"]))
    return server
