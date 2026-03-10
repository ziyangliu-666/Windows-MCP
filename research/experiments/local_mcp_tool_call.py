from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fastmcp import Client  # noqa: E402
from windows_mcp.__main__ import mcp  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call a Windows-MCP tool in-process.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--args", default="{}", help="JSON object of tool arguments.")
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    tool_args = json.loads(args.args)

    async with Client(mcp) as client:
        result = await client.call_tool(args.tool, tool_args, raise_on_error=False)

    if hasattr(result, "content"):
        payload = [block.model_dump() for block in result.content]
    else:
        payload = result

    print(json.dumps(payload, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
