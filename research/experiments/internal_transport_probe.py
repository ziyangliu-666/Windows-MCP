from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from windows_mcp.dev_hot import (  # noqa: E402
    WorkerSupervisor,
    _call_worker_tool,
    build_shell_mcp,
)
from windows_mcp.server_core import build_local_mcp, build_public_manifest_hash  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare internal worker transport behavior.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["local", "shell-hot", "worker-http", "worker-stdio", "worker-stdio-persistent"],
        help="Which runtime path to probe.",
    )
    parser.add_argument("--tool", default="Wait")
    parser.add_argument("--args", default='{"duration": 8}', help="JSON object of tool arguments.")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def normalize_result(result) -> object:
    if hasattr(result, "content"):
        return [block.model_dump() for block in result.content]
    return result


async def call_local(tool_name: str, tool_args: dict, timeout: float) -> dict[str, object]:
    mcp = build_local_mcp()
    start = time.time()
    async with Client(mcp) as client:
        result = await asyncio.wait_for(
            client.call_tool(tool_name, tool_args, raise_on_error=False),
            timeout=timeout,
        )
    return {"elapsed_seconds": time.time() - start, "payload": normalize_result(result)}


async def call_shell_hot(tool_name: str, tool_args: dict, timeout: float) -> dict[str, object]:
    manifest = await build_public_manifest_hash()
    shell = build_shell_mcp(WorkerSupervisor(expected_manifest_hash=manifest))
    start = time.time()
    async with Client(shell) as client:
        result = await asyncio.wait_for(
            client.call_tool(tool_name, tool_args, raise_on_error=False),
            timeout=timeout,
        )
    return {"elapsed_seconds": time.time() - start, "payload": normalize_result(result)}


async def call_worker_http(tool_name: str, tool_args: dict, timeout: float) -> dict[str, object]:
    manifest = await build_public_manifest_hash()
    supervisor = WorkerSupervisor(expected_manifest_hash=manifest)
    await asyncio.to_thread(supervisor.start_sync)
    try:
        worker = supervisor.active_worker
        if worker is None:
            raise RuntimeError("No active worker available")
        start = time.time()
        result = await asyncio.wait_for(
            _call_worker_tool(worker.url, tool_name, tool_args),
            timeout=timeout,
        )
        return {"elapsed_seconds": time.time() - start, "payload": result}
    finally:
        await asyncio.to_thread(supervisor.stop_sync)


async def call_worker_stdio(tool_name: str, tool_args: dict, timeout: float) -> dict[str, object]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "windows_mcp.__main__", "--internal-worker", "--transport", "stdio", "--generation", "1"],
        cwd=str(ROOT),
    )
    start = time.time()
    async with Client(transport) as client:
        result = await asyncio.wait_for(
            client.call_tool(tool_name, tool_args, raise_on_error=False),
            timeout=timeout,
        )
    return {"elapsed_seconds": time.time() - start, "payload": normalize_result(result)}


async def call_worker_stdio_persistent(tool_name: str, tool_args: dict, timeout: float) -> dict[str, object]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "windows_mcp.__main__", "--internal-worker", "--transport", "stdio", "--generation", "1"],
        cwd=str(ROOT),
    )
    start = time.time()
    async with Client(transport) as client:
        meta = await asyncio.wait_for(
            client.call_tool("__wmcp_worker_meta", {}, raise_on_error=False),
            timeout=10,
        )
        result = await asyncio.wait_for(
            client.call_tool(tool_name, tool_args, raise_on_error=False),
            timeout=timeout,
        )
        clipboard = await asyncio.wait_for(
            client.call_tool("Clipboard", {"mode": "get"}, raise_on_error=False),
            timeout=timeout,
        )
    return {
        "elapsed_seconds": time.time() - start,
        "meta": normalize_result(meta),
        "payload": normalize_result(result),
        "follow_up": normalize_result(clipboard),
    }


async def main() -> int:
    args = parse_args()
    tool_args = json.loads(args.args)

    try:
        match args.mode:
            case "local":
                payload = await call_local(args.tool, tool_args, args.timeout)
            case "shell-hot":
                payload = await call_shell_hot(args.tool, tool_args, args.timeout)
            case "worker-http":
                payload = await call_worker_http(args.tool, tool_args, args.timeout)
            case "worker-stdio":
                payload = await call_worker_stdio(args.tool, tool_args, args.timeout)
            case "worker-stdio-persistent":
                payload = await call_worker_stdio_persistent(args.tool, tool_args, args.timeout)
            case _:
                raise ValueError(f"Unsupported mode: {args.mode}")
        print(json.dumps({"ok": True, "mode": args.mode, **payload}, ensure_ascii=True, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": args.mode,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=True,
                default=str,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
