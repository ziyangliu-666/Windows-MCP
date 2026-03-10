from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from windows_mcp.desktop.service import Desktop  # noqa: E402
import windows_mcp.uia as uia  # noqa: E402


def find_document_center(
    desktop: Desktop, window_name: str, window_handle: int
) -> tuple[int, int]:
    state = desktop.get_state()
    active_window = state.active_window
    if active_window is None:
        raise RuntimeError("No active window after Notepad launch.")

    if active_window.name != window_name:
        raise RuntimeError(
            f"Expected active window '{window_name}', got '{active_window.name}'."
        )

    for node in state.tree_state.scrollable_nodes:
        if node.window_name == window_name and node.metadata.get("has_focused"):
            return node.center.x, node.center.y

    for node in state.tree_state.scrollable_nodes:
        if node.window_name == window_name:
            return node.center.x, node.center.y

    window = uia.ControlFromHandle(window_handle)
    document = window.DocumentControl(searchDepth=10, foundIndex=1)
    if document.Exists(maxSearchSeconds=2):
        box = document.BoundingRectangle
        return box.xcenter(), box.ycenter()

    raise RuntimeError(f"No document target found for '{window_name}'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Notepad typing reliability.")
    parser.add_argument(
        "--pre-type-delay",
        type=float,
        default=0.0,
        help="Seconds to wait after focusing the document and before typing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    probe_name = f"wmcp-type-probe-{int(time.time())}.txt"
    probe_path = Path.home() / "AppData" / "Local" / "Temp" / probe_name
    probe_path.write_text("", encoding="utf-8")
    probe_text = f"wmcp keyboard probe {int(time.time())}"

    subprocess.Popen(["notepad.exe", str(probe_path)])

    title_pattern = re.compile(rf"(?i).*{re.escape(probe_name)}.*")
    if not uia.WindowControl(RegexName=title_pattern.pattern).Exists(maxSearchSeconds=10):
        print(
            json.dumps(
                {
                    "status": "error",
                    "step": "launch",
                    "message": f"Notepad window for {probe_name} was not detected.",
                }
            )
        )
        return 1

    window = uia.WindowControl(RegexName=title_pattern.pattern)
    desktop = Desktop()
    desktop.bring_window_to_top(window.NativeWindowHandle)
    time.sleep(0.5)

    center = find_document_center(desktop, window.Name, window.NativeWindowHandle)
    if args.pre_type_delay > 0:
        time.sleep(args.pre_type_delay)
    desktop.type(center, probe_text, clear=False, press_enter=False)
    time.sleep(0.5)

    doc = window.DocumentControl(searchDepth=10, foundIndex=1)
    if not doc.Exists(maxSearchSeconds=2):
        print(
            json.dumps(
                {
                    "status": "error",
                    "step": "verify",
                    "message": "Document control not found after typing.",
                }
            )
        )
        return 1

    text_pattern = doc.GetTextPattern()
    if text_pattern is None:
        print(
            json.dumps(
                {
                    "status": "error",
                    "step": "verify",
                    "message": "Document control does not expose TextPattern.",
                }
            )
        )
        return 1

    actual_text = text_pattern.DocumentRange.GetText(-1)
    success = probe_text in actual_text
    print(
        json.dumps(
            {
                "status": "ok" if success else "mismatch",
                "probe_name": probe_name,
                "window_name": window.Name,
                "typed_text": probe_text,
                "pre_type_delay": args.pre_type_delay,
                "document_contains_text": success,
                "document_excerpt": actual_text[:200],
            },
            ensure_ascii=True,
        )
    )
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
