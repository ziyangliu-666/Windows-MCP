from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

import win32con
import win32gui
import win32process
from fuzzywuzzy import process


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from windows_mcp.desktop.service import Desktop  # noqa: E402
import windows_mcp.uia as uia  # noqa: E402


def describe_handle(handle: int) -> dict[str, object]:
    if not handle or not win32gui.IsWindow(handle):
        return {"handle": handle, "valid": False}
    thread_id, process_id = win32process.GetWindowThreadProcessId(handle)
    try:
        title = win32gui.GetWindowText(handle)
    except Exception:
        title = None
    try:
        class_name = win32gui.GetClassName(handle)
    except Exception:
        class_name = None
    return {
        "handle": handle,
        "valid": True,
        "title": title,
        "class_name": class_name,
        "thread_id": thread_id,
        "process_id": process_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose window focus switching.")
    parser.add_argument("--name", required=True, help="Fuzzy app/window name to target.")
    parser.add_argument("--poll-seconds", type=float, default=1.5)
    parser.add_argument("--poll-interval", type=float, default=0.1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    desktop = Desktop()
    desktop.get_state()

    window_list = [
        w for w in [desktop.desktop_state.active_window] + desktop.desktop_state.windows if w is not None
    ]
    windows = {window.name: window for window in window_list}
    matched_window = process.extractOne(args.name, list(windows.keys()), score_cutoff=70)
    if matched_window is None:
        print(json.dumps({"error": f"Application {args.name!r} not found."}, ensure_ascii=True))
        return 1

    window_name, score = matched_window
    target = windows[window_name]
    before = describe_handle(win32gui.GetForegroundWindow())

    bring_details: dict[str, object] = {
        "target_handle": target.handle,
        "target_before": describe_handle(target.handle),
    }

    if not win32gui.IsWindow(target.handle):
        raise RuntimeError(f"Invalid target handle: {target.handle}")

    if win32gui.IsIconic(target.handle):
        win32gui.ShowWindow(target.handle, win32con.SW_RESTORE)
        bring_details["restored"] = True
    else:
        bring_details["restored"] = False

    foreground_handle = win32gui.GetForegroundWindow()
    foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground_handle)
    target_thread, _ = win32process.GetWindowThreadProcessId(target.handle)
    bring_details["foreground_before"] = describe_handle(foreground_handle)
    bring_details["thread_ids"] = {
        "foreground_thread": foreground_thread,
        "target_thread": target_thread,
    }

    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    bring_details["allow_set_foreground_window_called"] = True

    attach_error: str | None = None
    attached = False
    try:
        if foreground_thread and target_thread and foreground_thread != target_thread:
            try:
                win32process.AttachThreadInput(foreground_thread, target_thread, True)
                attached = True
            except Exception as exc:
                attach_error = str(exc)
                bring_details["attach_thread_input_error"] = attach_error
        else:
            bring_details["attach_thread_input_skipped"] = True

        try:
            bring_details["set_foreground_window_result"] = bool(win32gui.SetForegroundWindow(target.handle))
        except Exception as exc:
            bring_details["set_foreground_window_error"] = str(exc)

        try:
            win32gui.BringWindowToTop(target.handle)
            bring_details["bring_window_to_top_called"] = True
        except Exception as exc:
            bring_details["bring_window_to_top_error"] = str(exc)

        try:
            win32gui.SetWindowPos(
                target.handle,
                win32con.HWND_TOP,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
            )
            bring_details["set_window_pos_called"] = True
        except Exception as exc:
            bring_details["set_window_pos_error"] = str(exc)
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(foreground_thread, target_thread, False)
                bring_details["attach_thread_input_detached"] = True
            except Exception as exc:
                bring_details["attach_thread_input_detach_error"] = str(exc)

    if attach_error is not None:
        try:
            control = uia.ControlFromHandle(target.handle)
            control.SetFocus()
            bring_details["uia_set_focus_called"] = True
        except Exception as exc:
            bring_details["uia_set_focus_error"] = str(exc)
        try:
            bring_details["fallback_set_foreground_window_result"] = bool(win32gui.SetForegroundWindow(target.handle))
        except Exception as exc:
            bring_details["fallback_set_foreground_window_error"] = str(exc)
        try:
            win32gui.BringWindowToTop(target.handle)
            bring_details["fallback_bring_window_to_top_called"] = True
        except Exception as exc:
            bring_details["fallback_bring_window_to_top_error"] = str(exc)

    observed = []
    deadline = time.time() + args.poll_seconds
    while time.time() < deadline:
        observed.append(describe_handle(win32gui.GetForegroundWindow()))
        time.sleep(args.poll_interval)

    payload = {
        "requested_name": args.name,
        "matched_window_name": window_name,
        "match_score": score,
        "target_window": {
            "name": target.name,
            "handle": target.handle,
            "process_id": target.process_id,
        },
        "foreground_before": before,
        "foreground_after": observed[-1] if observed else describe_handle(win32gui.GetForegroundWindow()),
        "observed_foreground_sequence": observed,
        "switch_success": bool(observed and observed[-1].get("handle") == target.handle),
        "bring_details": bring_details,
    }
    print(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
