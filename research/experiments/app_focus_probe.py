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
    parser = argparse.ArgumentParser(description="Probe app launch/switch focus behavior.")
    parser.add_argument("--mode", choices=["launch", "switch"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--settle-seconds", type=float, default=0.75)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    desktop = Desktop()

    before_state = desktop.get_state()
    before_active = before_state.active_window.name if before_state.active_window else None

    result = desktop.app(mode=args.mode, name=args.name)
    time.sleep(args.settle_seconds)

    after_state = desktop.get_state()
    after_active = after_state.active_window.name if after_state.active_window else None

    print(
        json.dumps(
            {
                "mode": args.mode,
                "name": args.name,
                "result": result,
                "before_active": before_active,
                "after_active": after_active,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
