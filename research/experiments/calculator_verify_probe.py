from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from windows_mcp.desktop.service import Desktop  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Calculator result verification.")
    parser.add_argument(
        "--sequence",
        default='["Seven", "Plus", "Eight", "Equals"]',
        help="JSON array of Calculator button names to click.",
    )
    parser.add_argument(
        "--expect-substring",
        default="Display is 15",
        help="Expected substring in Calculator informative text after the sequence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sequence = json.loads(args.sequence)

    desktop = Desktop()
    launch_result = desktop.app(mode="launch", name="Calculator")
    state = desktop.get_state()

    button_lookup = {
        node.name: (node.center.x, node.center.y)
        for node in state.tree_state.interactive_nodes
        if node.window_name == "Calculator"
    }
    missing = [button for button in sequence if button not in button_lookup]
    if missing:
        print(
            json.dumps(
                {
                    "status": "error",
                    "step": "resolve_buttons",
                    "launch_result": launch_result,
                    "missing": missing,
                },
                ensure_ascii=True,
            )
        )
        return 1

    for button_name in sequence:
        desktop.click(button_lookup[button_name], button="left", clicks=1)
        time.sleep(0.2)

    state = desktop.get_state()
    displays = [
        node.text
        for node in state.tree_state.informative_nodes
        if node.window_name == "Calculator" and "Display is" in node.text
    ]
    success = any(args.expect_substring in display for display in displays)
    print(
        json.dumps(
            {
                "status": "ok" if success else "mismatch",
                "launch_result": launch_result,
                "sequence": sequence,
                "expect_substring": args.expect_substring,
                "active_window": state.active_window.name if state.active_window else None,
                "displays": displays,
            },
            ensure_ascii=True,
        )
    )
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
