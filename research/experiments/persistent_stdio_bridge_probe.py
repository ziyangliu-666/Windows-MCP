from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class PersistentStdioBridge:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ready = threading.Event()
        self._transport: StdioTransport | None = None
        self._client: Client | None = None

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("Persistent stdio bridge did not become ready")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._connect())
        self._loop.run_forever()

    async def _connect(self) -> None:
        self._transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "windows_mcp.__main__",
                "--internal-worker",
                "--transport",
                "stdio",
                "--generation",
                "1",
            ],
            cwd=str(ROOT),
        )
        self._client = Client(self._transport)
        await self._client.__aenter__()
        self._ready.set()

    def call(self, tool_name: str, arguments: dict[str, Any], timeout: float = 20.0):
        if not self._client:
            raise RuntimeError("Bridge client is not ready")
        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(tool_name, arguments, raise_on_error=False),
            self._loop,
        )
        return future.result(timeout=timeout)

    def close(self) -> None:
        if not self._client:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._client.__aexit__(None, None, None),
            self._loop,
        )
        future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def normalize_result(result) -> object:
    if hasattr(result, "content"):
        return [block.model_dump() for block in result.content]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype a persistent stdio MCP bridge.")
    parser.add_argument(
        "--sequence",
        default='[["__wmcp_worker_meta", {}], ["Wait", {"duration": 8}], ["Clipboard", {"mode": "get"}]]',
        help="JSON list of [tool_name, arguments] pairs.",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sequence = json.loads(args.sequence)
    bridge = PersistentStdioBridge()
    start = time.time()
    results: list[dict[str, object]] = []
    try:
        bridge.start()
        for tool_name, tool_args in sequence:
            call_start = time.time()
            result = bridge.call(tool_name, tool_args, timeout=args.timeout)
            results.append(
                {
                    "tool": tool_name,
                    "elapsed_seconds": round(time.time() - call_start, 3),
                    "result": normalize_result(result),
                }
            )
        print(
            json.dumps(
                {"ok": True, "total_elapsed_seconds": round(time.time() - start, 3), "results": results},
                ensure_ascii=True,
                default=str,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "total_elapsed_seconds": round(time.time() - start, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "results": results,
                },
                ensure_ascii=True,
                default=str,
            )
        )
        return 1
    finally:
        try:
            bridge.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
