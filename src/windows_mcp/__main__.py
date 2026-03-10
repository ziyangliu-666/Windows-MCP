from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from enum import Enum

import click
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.providers.proxy import ProxyClient

from windows_mcp.auth import AuthClient
from windows_mcp.dev_hot import (
    WorkerSupervisor,
    build_shell_mcp,
    build_worker_mcp,
    get_git_sha,
    get_source_hash,
    get_source_tree_dirty,
)
from windows_mcp.server_core import build_local_mcp, build_public_manifest_hash


@dataclass
class Config:
    mode: str
    sandbox_id: str = field(default="")
    api_key: str = field(default="")


class Transport(Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    def __str__(self):
        return self.value


class Mode(Enum):
    LOCAL = "local"
    REMOTE = "remote"

    def __str__(self):
        return self.value


class DevMode(Enum):
    OFF = "off"
    HOT = "hot"

    def __str__(self):
        return self.value

mcp = build_local_mcp()


def run_server(server: FastMCP, transport: str, host: str, port: int) -> None:
    match transport:
        case Transport.STDIO.value:
            server.run(transport=Transport.STDIO.value, show_banner=False)
        case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
            server.run(transport=transport, host=host, port=port, show_banner=False)
        case _:
            raise ValueError(f"Invalid transport: {transport}")


@click.command()
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(
        [Transport.STDIO.value, Transport.SSE.value, Transport.STREAMABLE_HTTP.value]
    ),
    default=Transport.STDIO.value,
)
@click.option(
    "--host",
    help="Host to bind the SSE/Streamable HTTP server.",
    default="localhost",
    type=str,
    show_default=True,
)
@click.option(
    "--port",
    help="Port to bind the SSE/Streamable HTTP server.",
    default=8000,
    type=int,
    show_default=True,
)
@click.option(
    "--dev",
    help="Development runtime mode.",
    type=click.Choice([DevMode.OFF.value, DevMode.HOT.value]),
    default=DevMode.OFF.value,
    show_default=True,
)
@click.option("--internal-worker", is_flag=True, default=False, hidden=True)
@click.option("--generation", type=int, default=0, hidden=True)
def main(transport: str, host: str, port: int, dev: str, internal_worker: bool, generation: int):
    manifest_hash = asyncio.run(build_public_manifest_hash())
    git_sha = get_git_sha()
    source_hash = get_source_hash()
    source_dirty = get_source_tree_dirty()

    if internal_worker:
        worker_mcp = build_worker_mcp(
            generation=generation,
            manifest_hash=manifest_hash,
            git_sha=git_sha,
            source_hash=source_hash,
            source_dirty=source_dirty,
        )
        run_server(worker_mcp, transport, host, port)
        return

    config = Config(
        mode=os.getenv("MODE", Mode.LOCAL.value).lower(),
        sandbox_id=os.getenv("SANDBOX_ID", ""),
        api_key=os.getenv("API_KEY", ""),
    )

    match config.mode:
        case Mode.LOCAL.value:
            if dev == DevMode.HOT.value:
                supervisor = WorkerSupervisor(expected_manifest_hash=manifest_hash)
                shell_mcp = build_shell_mcp(supervisor)
                run_server(shell_mcp, transport, host, port)
            else:
                run_server(mcp, transport, host, port)
        case Mode.REMOTE.value:
            if dev == DevMode.HOT.value:
                raise ValueError("Dev hot mode is only supported in MODE=local.")
            if not config.sandbox_id:
                raise ValueError("SANDBOX_ID is required for MODE: remote")
            if not config.api_key:
                raise ValueError("API_KEY is required for MODE: remote")
            client = AuthClient(api_key=config.api_key, sandbox_id=config.sandbox_id)
            client.authenticate()
            backend = StreamableHttpTransport(url=client.proxy_url, headers=client.proxy_headers)
            proxy_mcp = FastMCP.as_proxy(ProxyClient(backend), name="windows-mcp")
            run_server(proxy_mcp, transport, host, port)
        case _:
            raise ValueError(f"Invalid mode: {config.mode}")


if __name__ == "__main__":
    main()
