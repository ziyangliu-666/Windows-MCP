from __future__ import annotations

import asyncio

from fastmcp import Client, FastMCP

from windows_mcp.server_core import (
    _normalize_loc_argument,
    register_public_tools,
    type_handler,
)


class RecordingDesktop:
    def __init__(self):
        self.type_calls: list[dict] = []

    def type(self, *, loc, text, caret_position, clear, press_enter):
        self.type_calls.append(
            {
                "loc": loc,
                "text": text,
                "caret_position": caret_position,
                "clear": clear,
                "press_enter": press_enter,
            }
        )


class RecordingRuntime:
    def __init__(self):
        self.desktop = RecordingDesktop()


def test_normalize_loc_argument_accepts_string_forms():
    assert _normalize_loc_argument("500,400") == [500, 400]
    assert _normalize_loc_argument("(500, 400)") == [500, 400]
    assert _normalize_loc_argument("[500,400]") == [500, 400]


def test_type_handler_normalizes_string_loc():
    runtime = RecordingRuntime()

    result = type_handler(runtime, text="x", loc="500,400")

    assert result == "Typed x at (500,400)."
    assert runtime.desktop.type_calls == [
        {
            "loc": [500, 400],
            "text": "x",
            "caret_position": "idle",
            "clear": False,
            "press_enter": False,
        }
    ]


def test_public_type_tool_accepts_string_loc():
    calls: list[tuple[str, dict]] = []

    async def invoker(tool_name: str, args: dict, _ctx):
        calls.append((tool_name, args))
        return "ok"

    mcp = FastMCP(name="test-coords")
    register_public_tools(mcp, invoker)

    async def run():
        async with Client(mcp) as client:
            result = await client.call_tool(
                "Type",
                {"text": "x", "loc": "500,400"},
                raise_on_error=False,
            )
        payload = [block.model_dump() for block in result.content]
        assert payload[0]["text"] == "ok"

    asyncio.run(run())

    assert calls == [
        (
            "Type",
            {
                "text": "x",
                "loc": "500,400",
                "label": None,
                "clear": False,
                "caret_position": "idle",
                "press_enter": False,
            },
        )
    ]
