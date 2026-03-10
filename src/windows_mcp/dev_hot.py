from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import types
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.utilities.types import Image

from windows_mcp.server_core import (
    INSTRUCTIONS,
    LocalRuntime,
    build_public_manifest_hash,
    build_local_invoker,
    close_local_runtime,
    create_local_runtime,
    decode_image_content_data,
    register_dev_server_tool,
    register_public_tools,
)


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
INTERNAL_META_TOOL = "__wmcp_worker_meta"
HOT_WORKER_TRANSPORT_STDIO_PERSISTENT = "stdio-persistent"
HOT_WORKER_TRANSPORT_HTTP = "streamable-http"
DEV_TRACE_LOCK = threading.Lock()
SHELL_OWNED_PATHS = (
    Path("src/windows_mcp/__main__.py"),
    Path("src/windows_mcp/dev_hot.py"),
    Path("src/windows_mcp/server_core.py"),
)
DEV_SHELL_CALLS_PATH = ROOT / "src" / "windows_mcp" / "dev_shell_calls.py"


def get_git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def get_source_hash() -> str:
    src_root = ROOT / "src" / "windows_mcp"
    digest = hashlib.sha256()
    for path in sorted(src_root.rglob("*.py")):
        relative_path = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def get_source_tree_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "src/windows_mcp"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except Exception:
        pass
    return False


def _get_dev_trace_file() -> Path:
    configured = os.getenv("WMCP_DEV_TRACE_FILE", "").strip()
    if configured:
        return Path(configured)
    return ROOT / "research" / "runtime" / "devserver_trace.jsonl"


def _append_dev_trace(event: str, **payload: Any) -> None:
    record = {
        "timestamp": round(time.time(), 3),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "event": event,
        **payload,
    }
    trace_file = _get_dev_trace_file()
    try:
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(record, ensure_ascii=True, sort_keys=True)
        with DEV_TRACE_LOCK:
            with trace_file.open("a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.write("\n")
    except Exception:
        logger.exception("Failed to append dev trace", extra={"event": event})


def _parse_dev_call_arguments(arguments_json: str | None) -> dict[str, Any]:
    raw = (arguments_json or "{}").strip() or "{}"
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("DevServer call arguments_json must decode to a JSON object")
    return parsed


def _load_dev_shell_module(module_path: Path):
    if not module_path.exists():
        return None
    module_name = f"_wmcp_dev_shell_calls_{time.time_ns()}"
    source = module_path.read_text(encoding="utf-8")
    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    code = compile(source, str(module_path), "exec")
    exec(code, module.__dict__)
    return module


def _normalize_dev_shell_registry(raw_registry: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_registry, dict):
        raise ValueError("Dev shell call registry must be a dict")

    registry: dict[str, dict[str, Any]] = {}
    for name, value in raw_registry.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Dev shell call names must be non-empty strings")

        description: str | None = None
        handler = value
        if isinstance(value, dict):
            handler = value.get("callable")
            description = value.get("description")
        elif isinstance(value, tuple) and len(value) == 2:
            handler, description = value

        if not callable(handler):
            raise ValueError(f"Dev shell call {name!r} must map to a callable")

        registry[name] = {
            "callable": handler,
            "description": description,
        }
    return registry


def _load_dynamic_dev_shell_registry(module_path: Path) -> dict[str, dict[str, Any]]:
    module = _load_dev_shell_module(module_path)
    if module is None:
        return {}

    if hasattr(module, "register_calls"):
        raw_registry = module.register_calls()
    else:
        raw_registry = getattr(module, "CALLS", {})
    return _normalize_dev_shell_registry(raw_registry)


def _hash_paths(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(paths):
        absolute_path = ROOT / relative_path
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(absolute_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def get_shell_code_hash() -> str:
    return _hash_paths(SHELL_OWNED_PATHS)


def get_shell_code_file_hashes() -> dict[str, str]:
    return {
        relative_path.as_posix(): hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        for relative_path in sorted(SHELL_OWNED_PATHS)
    }


def get_shell_code_dirty_status(timeout_seconds: float = 1.0) -> tuple[bool | None, str]:
    try:
        relative_paths = [path.as_posix() for path in SHELL_OWNED_PATHS]
        worktree = subprocess.run(
            ["git", "diff", "--quiet", "--no-ext-diff", "--", *relative_paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        if worktree.returncode == 1:
            return True, "ok"
        if worktree.returncode != 0:
            return None, f"worktree_exit_{worktree.returncode}"

        index = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--no-ext-diff", "--", *relative_paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        if index.returncode == 1:
            return True, "ok"
        if index.returncode != 0:
            return None, f"index_exit_{index.returncode}"
        return False, "ok"
    except subprocess.TimeoutExpired:
        logger.warning("Timed out checking shell code dirty state")
        return None, "timeout"
    except Exception:
        logger.exception("Failed to check shell code dirty state")
        return None, "error"


def get_shell_code_dirty(timeout_seconds: float = 1.0) -> bool | None:
    dirty, _status = get_shell_code_dirty_status(timeout_seconds=timeout_seconds)
    return dirty


def _find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _worker_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/mcp"


def _format_epoch_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _coerce_worker_result(tool_name: str, result) -> object:
    if not hasattr(result, "content"):
        return result

    content = result.content
    if tool_name != "Snapshot" and len(content) == 1 and getattr(content[0], "type", None) == "text":
        return content[0].text

    converted = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            converted.append(block.text)
        elif block_type == "image":
            mime_type = getattr(block, "mimeType", "image/png") or "image/png"
            image_format = mime_type.split("/")[-1]
            converted.append(
                Image(
                    data=decode_image_content_data(block.data),
                    format=image_format,
                )
            )
        else:
            raise ValueError(f"Unsupported worker content block type: {block_type}")
    return converted


async def _call_worker_tool(url: str, tool_name: str, arguments: dict) -> object:
    transport = StreamableHttpTransport(
        url=url,
        httpx_client_factory=lambda **kwargs: httpx.AsyncClient(trust_env=False, **kwargs),
    )
    return await _call_worker_transport_tool(transport, tool_name, arguments)


async def _call_worker_transport_tool(transport, tool_name: str, arguments: dict) -> object:
    async with Client(transport) as client:
        result = await client.call_tool(tool_name, arguments, raise_on_error=True)
    return _coerce_worker_result(tool_name, result)


async def _call_worker_json_tool(url: str, tool_name: str, arguments: dict | None = None) -> dict[str, Any]:
    payload = await _call_worker_tool(url, tool_name, arguments or {})
    if not isinstance(payload, str):
        raise ValueError(f"Expected JSON text from {tool_name}, got {type(payload)!r}")
    return json.loads(payload)


async def _call_worker_json_tool_via_transport(
    transport,
    tool_name: str,
    arguments: dict | None = None,
) -> dict[str, Any]:
    payload = await _call_worker_transport_tool(transport, tool_name, arguments or {})
    if not isinstance(payload, str):
        raise ValueError(f"Expected JSON text from {tool_name}, got {type(payload)!r}")
    return json.loads(payload)


class PersistentStdioWorkerBridge:
    def __init__(self, generation: int):
        self.generation = generation
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"WindowsMcpStdioWorker-{generation}",
            daemon=True,
        )
        self._ready = threading.Event()
        self._transport: StdioTransport | None = None
        self._client: Client | None = None
        self._startup_error: Exception | None = None
        self._closed = False

    def start(self, timeout_seconds: int) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=timeout_seconds):
            raise RuntimeError("Persistent stdio worker bridge did not become ready")
        if self._startup_error is not None:
            raise RuntimeError(str(self._startup_error))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._connect())
        self._loop.run_forever()

    async def _connect(self) -> None:
        try:
            self._transport = StdioTransport(
                command=sys.executable,
                args=[
                    "-m",
                    "windows_mcp.__main__",
                    "--internal-worker",
                    "--transport",
                    "stdio",
                    "--generation",
                    str(self.generation),
                ],
                cwd=str(ROOT),
            )
            self._client = Client(self._transport)
            await self._client.__aenter__()
        except Exception as exc:
            self._startup_error = exc
        finally:
            self._ready.set()

    def call_tool_sync(self, tool_name: str, arguments: dict, timeout_seconds: int) -> object:
        if self._closed:
            raise RuntimeError("Persistent stdio worker bridge is closed")
        if self._client is None:
            raise RuntimeError("Persistent stdio worker client is not ready")
        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(tool_name, arguments, raise_on_error=True),
            self._loop,
        )
        result = future.result(timeout=timeout_seconds)
        return _coerce_worker_result(tool_name, result)

    def close(self, timeout_seconds: int = 10) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._client.__aexit__(None, None, None),
                self._loop,
            )
            future.result(timeout=timeout_seconds)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout_seconds)

    def is_alive(self) -> bool:
        return not self._closed and self._thread.is_alive() and self._startup_error is None


@dataclass
class WorkerProcess:
    generation: int
    transport_kind: str
    manifest_hash: str
    git_sha: str | None
    source_hash: str | None
    source_dirty: bool
    started_at_epoch: float
    process: subprocess.Popen | None = None
    port: int | None = None
    process_pid: int | None = None
    stdio_bridge: PersistentStdioWorkerBridge | None = None

    @property
    def url(self) -> str:
        if self.port is None:
            raise RuntimeError("HTTP worker URL is unavailable for stdio workers")
        return _worker_url(self.port)

    def is_alive(self) -> bool:
        if self.process is not None:
            return self.process.poll() is None
        if self.stdio_bridge is not None:
            return self.stdio_bridge.is_alive()
        return False


class ReloadWatcher(threading.Thread):
    def __init__(self, supervisor: "WorkerSupervisor", poll_interval: float = 0.75):
        super().__init__(name="WindowsMcpReloadWatcher", daemon=True)
        self.supervisor = supervisor
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._last_snapshot = self._snapshot()
        self._last_change_time = 0.0
        self._pending_reload = False

    def stop(self) -> None:
        self._stop_event.set()

    def _snapshot(self) -> dict[str, float]:
        snapshot: dict[str, float] = {}
        src_root = ROOT / "src" / "windows_mcp"
        for path in src_root.rglob("*.py"):
            try:
                snapshot[str(path)] = path.stat().st_mtime
            except FileNotFoundError:
                continue
        return snapshot

    def run(self) -> None:
        while not self._stop_event.wait(self.poll_interval):
            current = self._snapshot()
            if current != self._last_snapshot:
                self._last_snapshot = current
                self._last_change_time = time.time()
                self._pending_reload = True
                continue

            if self._pending_reload and time.time() - self._last_change_time >= self.poll_interval:
                self._pending_reload = False
                try:
                    self.supervisor.reload_sync(trigger="watcher")
                except Exception:
                    logger.exception("Watcher-triggered reload failed")


class WorkerSupervisor:
    def __init__(self, expected_manifest_hash: str):
        self.expected_manifest_hash = expected_manifest_hash
        self.internal_worker_transport = os.getenv(
            "WMCP_HOT_WORKER_TRANSPORT",
            HOT_WORKER_TRANSPORT_STDIO_PERSISTENT,
        ).strip().lower()
        self.condition = threading.Condition()
        self.active_calls = 0
        self.reloading = False
        self.active_worker: WorkerProcess | None = None
        self.reload_count = 0
        self.last_reload_status = "not_started"
        self.last_reload_started_at: float | None = None
        self.last_reload_finished_at: float | None = None
        self.last_reload_error: str | None = None
        self.manifest_drift_detected = False
        self.pending_manifest_hash: str | None = None
        self.reconnect_required = False
        self.watcher: ReloadWatcher | None = None
        self.shell_started_at_epoch = time.time()
        self.shell_session_id = f"{os.getpid()}-{int(self.shell_started_at_epoch * 1000)}"
        self.shell_loaded_source_hash = get_shell_code_hash()
        self.shell_loaded_file_hashes = get_shell_code_file_hashes()
        self.shell_restart_required = False
        self.shell_source_dirty: bool | None = None
        self.shell_source_dirty_check = "disabled"

    def _shell_runtime_identity(self) -> dict[str, Any]:
        return {
            "shell_pid": os.getpid(),
            "shell_session_id": self.shell_session_id,
            "shell_started_at_epoch": self.shell_started_at_epoch,
            "shell_started_at_iso": _format_epoch_iso(self.shell_started_at_epoch),
            "shell_python_executable": sys.executable,
        }

    def start_sync(self) -> dict[str, Any]:
        with self.condition:
            if self.active_worker and self.active_worker.is_alive():
                return self.health_snapshot()

        worker = self._spawn_worker(generation=1, timeout_seconds=15)
        with self.condition:
            self.active_worker = worker
            self.last_reload_status = "ready"
            self.last_reload_finished_at = time.time()
            return self.health_snapshot()

    def stop_sync(self) -> None:
        watcher = self.watcher
        if watcher:
            watcher.stop()
            watcher.join(timeout=2.0)
            self.watcher = None

        with self.condition:
            worker = self.active_worker
            self.active_worker = None

        if worker:
            self._stop_worker(worker)

    def start_watcher(self) -> None:
        if self.watcher is None:
            self.watcher = ReloadWatcher(self)
            self.watcher.start()

    def call_tool_sync(self, tool_name: str, arguments: dict) -> object:
        worker = self._acquire_worker_for_call()
        try:
            if worker.transport_kind == HOT_WORKER_TRANSPORT_STDIO_PERSISTENT:
                if worker.stdio_bridge is None:
                    raise RuntimeError("Persistent stdio worker bridge is missing")
                return worker.stdio_bridge.call_tool_sync(
                    tool_name,
                    arguments,
                    timeout_seconds=120,
                )
            return asyncio.run(_call_worker_tool(worker.url, tool_name, arguments))
        except Exception:
            with self.condition:
                if self.active_worker is worker:
                    self.active_worker = None
            raise
        finally:
            with self.condition:
                self.active_calls -= 1
                self.condition.notify_all()

    def dev_server_sync(
        self,
        *,
        mode: str,
        wait_for_ready: bool = True,
        timeout_seconds: int = 15,
        name: str | None = None,
        arguments_json: str = "{}",
        load_latest: bool = True,
    ) -> str:
        _append_dev_trace(
            "dev_server_sync_enter",
            mode=mode,
            timeout_seconds=timeout_seconds,
            wait_for_ready=wait_for_ready,
            name=name,
            load_latest=load_latest,
        )
        if mode == "health":
            payload = json.dumps(self.health_snapshot(), ensure_ascii=True, sort_keys=True)
            _append_dev_trace("dev_server_sync_health_ok", payload_length=len(payload))
            return payload
        if mode == "reload":
            if wait_for_ready:
                snapshot = self.reload_sync(trigger="tool", timeout_seconds=timeout_seconds)
                payload = json.dumps(snapshot, ensure_ascii=True, sort_keys=True)
                _append_dev_trace("dev_server_sync_reload_ok", payload_length=len(payload))
                return payload

            thread = threading.Thread(
                target=self.reload_sync,
                kwargs={"trigger": "tool-background", "timeout_seconds": timeout_seconds},
                daemon=True,
            )
            thread.start()
            payload = json.dumps(
                {"status": "accepted", "reload_in_progress": True},
                ensure_ascii=True,
                sort_keys=True,
            )
            _append_dev_trace("dev_server_sync_reload_accepted", payload_length=len(payload))
            return payload
        if mode == "call":
            payload = json.dumps(
                self._call_dev_server_function(
                    name=name,
                    arguments_json=arguments_json,
                    load_latest=load_latest,
                ),
                ensure_ascii=True,
                sort_keys=True,
            )
            _append_dev_trace("dev_server_sync_call_ok", payload_length=len(payload), name=name)
            return payload
        _append_dev_trace("dev_server_sync_invalid_mode", mode=mode)
        raise ValueError(f"Unsupported DevServer mode: {mode}")

    def _native_dev_server_calls(self) -> dict[str, dict[str, Any]]:
        return {
            "list_calls": {
                "callable": self._dev_call_list_calls,
                "description": "List native and dynamically loaded DevServer call targets.",
            },
            "describe_restart_boundary": {
                "callable": self._dev_call_describe_restart_boundary,
                "description": "Explain whether shell restart or host reconnect is currently required.",
            },
            "describe_shell_file_delta": {
                "callable": self._dev_call_describe_shell_file_delta,
                "description": "Report which shell-owned files differ from the hashes loaded by the current shell process.",
            },
        }

    def _call_dev_server_function(
        self,
        *,
        name: str | None,
        arguments_json: str,
        load_latest: bool,
    ) -> dict[str, Any]:
        if not name:
            raise ValueError("DevServer mode='call' requires a non-empty name")

        arguments = _parse_dev_call_arguments(arguments_json)
        native_registry = self._native_dev_server_calls()
        if name in native_registry:
            handler = native_registry[name]["callable"]
            return {
                "name": name,
                "source": "native",
                "result": handler(arguments, load_latest=load_latest),
            }

        dynamic_registry = _load_dynamic_dev_shell_registry(DEV_SHELL_CALLS_PATH) if load_latest else {}
        if name in dynamic_registry:
            handler = dynamic_registry[name]["callable"]
            return {
                "name": name,
                "source": "dynamic",
                "module_path": DEV_SHELL_CALLS_PATH.as_posix(),
                "result": handler(self, arguments),
            }

        available_calls = sorted([*native_registry.keys(), *dynamic_registry.keys()])
        raise ValueError(
            f"Unknown DevServer call {name!r}. Available calls: {', '.join(available_calls) or '(none)'}"
        )

    def _dev_call_list_calls(self, arguments: dict[str, Any], *, load_latest: bool) -> dict[str, Any]:
        module_path = DEV_SHELL_CALLS_PATH
        dynamic_registry: dict[str, dict[str, Any]] = {}
        dynamic_error: str | None = None
        if load_latest:
            try:
                dynamic_registry = _load_dynamic_dev_shell_registry(module_path)
            except Exception as exc:
                dynamic_error = str(exc)

        native_registry = self._native_dev_server_calls()
        calls = [
            {
                "name": name,
                "source": "native",
                "description": entry.get("description"),
            }
            for name, entry in sorted(native_registry.items())
        ]
        calls.extend(
            {
                "name": name,
                "source": "dynamic",
                "description": entry.get("description"),
            }
            for name, entry in sorted(dynamic_registry.items())
        )
        return {
            "calls": calls,
            "dynamic_module_path": module_path.as_posix(),
            "dynamic_module_exists": module_path.exists(),
            "dynamic_module_error": dynamic_error,
            "load_latest": load_latest,
            "requested_arguments": arguments,
        }

    def _dev_call_describe_restart_boundary(
        self,
        arguments: dict[str, Any],
        *,
        load_latest: bool,
    ) -> dict[str, Any]:
        snapshot = self.health_snapshot()
        shell_file_delta = self._shell_file_delta_payload(arguments)
        changed_shell_paths = shell_file_delta["changed_shell_paths"]
        current_file_hashes = shell_file_delta["shell_current_file_hashes"]
        return {
            "shell_restart_required": snapshot["shell_restart_required"],
            "reconnect_required": snapshot["reconnect_required"],
            "shell_loaded_source_hash": snapshot["shell_loaded_source_hash"],
            "shell_current_source_hash": snapshot["shell_current_source_hash"],
            "shell_source_dirty": snapshot["shell_source_dirty"],
            "shell_source_dirty_check": snapshot["shell_source_dirty_check"],
            "shell_owned_paths": [path.as_posix() for path in SHELL_OWNED_PATHS],
            "shell_loaded_file_hashes": dict(sorted(self.shell_loaded_file_hashes.items())),
            "shell_current_file_hashes": dict(sorted(current_file_hashes.items())),
            "changed_shell_paths": changed_shell_paths,
            "dynamic_module_path": DEV_SHELL_CALLS_PATH.as_posix(),
            "dynamic_load_supported": True,
            "load_latest": load_latest,
            "requested_arguments": arguments,
            **self._shell_runtime_identity(),
        }

    def _dev_call_describe_shell_file_delta(
        self,
        arguments: dict[str, Any],
        *,
        load_latest: bool,
    ) -> dict[str, Any]:
        return self._shell_file_delta_payload(arguments)

    def _shell_file_delta_payload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current_file_hashes = get_shell_code_file_hashes()
        return {
            "dynamic_module_path": DEV_SHELL_CALLS_PATH.as_posix(),
            "shell_owned_paths": [path.as_posix() for path in SHELL_OWNED_PATHS],
            "shell_loaded_file_hashes": dict(sorted(self.shell_loaded_file_hashes.items())),
            "shell_current_file_hashes": dict(sorted(current_file_hashes.items())),
            "changed_shell_paths": [
                path
                for path, current_hash in current_file_hashes.items()
                if self.shell_loaded_file_hashes.get(path) != current_hash
            ],
            "fallback_mode": "loaded_hashes",
            "requested_arguments": arguments,
            **self._shell_runtime_identity(),
        }

    def reload_sync(self, trigger: str, timeout_seconds: int = 15) -> dict[str, Any]:
        candidate: WorkerProcess | None = None
        old_worker: WorkerProcess | None = None
        try:
            with self.condition:
                while self.reloading:
                    self.condition.wait()
                self.reloading = True
                self.last_reload_started_at = time.time()
                self.last_reload_error = None
                self.last_reload_status = "reloading"
                while self.active_calls > 0:
                    self.condition.wait()
                old_worker = self.active_worker
                next_generation = (old_worker.generation if old_worker else 0) + 1

            # Windows UI Automation hooks are not reliably shareable across two concurrent workers.
            # Quiesce the shell, stop the old worker, then bring up the replacement worker.
            if old_worker:
                self._stop_worker(old_worker)
                with self.condition:
                    if self.active_worker is old_worker:
                        self.active_worker = None
                # UI Automation event handlers can take a moment to fully release on Windows.
                time.sleep(0.75)

            candidate = self._spawn_worker(next_generation, timeout_seconds=timeout_seconds)
            if candidate.manifest_hash != self.expected_manifest_hash:
                self._stop_worker(candidate)
                with self.condition:
                    self.manifest_drift_detected = True
                    self.pending_manifest_hash = candidate.manifest_hash
                    self.reconnect_required = True
                    self.active_worker = None
                    self.last_reload_status = "manifest_drift"
                    self.last_reload_finished_at = time.time()
                    self.reloading = False
                    self.condition.notify_all()
                    return self.health_snapshot()

            with self.condition:
                self.active_worker = candidate
                self.reload_count += 1
                self.manifest_drift_detected = False
                self.pending_manifest_hash = None
                self.reconnect_required = False
                self.last_reload_status = f"reloaded:{trigger}"
                self.last_reload_finished_at = time.time()
                self.reloading = False
                self.condition.notify_all()

            return self.health_snapshot()
        except Exception as exc:
            logger.exception("Worker reload failed")
            if candidate:
                self._stop_worker(candidate)
            with self.condition:
                self.active_worker = None
                self.last_reload_error = str(exc)
                self.last_reload_status = "failed"
                self.last_reload_finished_at = time.time()
                self.reloading = False
                self.condition.notify_all()
                return self.health_snapshot()

    def health_snapshot(self) -> dict[str, Any]:
        started_at = time.time()
        _append_dev_trace("health_snapshot_enter")
        hash_started_at = time.time()
        current_shell_hash = get_shell_code_hash()
        hash_ms = round((time.time() - hash_started_at) * 1000, 3)
        dirty_ms = 0.0
        lock_started_at = time.time()
        with self.condition:
            lock_wait_ms = round((time.time() - lock_started_at) * 1000, 3)
            worker = self.active_worker
            self.shell_restart_required = current_shell_hash != self.shell_loaded_source_hash
            snapshot = {
                "dev_mode": "hot",
                "shell_loaded_source_hash": self.shell_loaded_source_hash,
                "shell_current_source_hash": current_shell_hash,
                "shell_source_dirty": self.shell_source_dirty,
                "shell_source_dirty_check": self.shell_source_dirty_check,
                "shell_restart_required": self.shell_restart_required,
                "active_worker_transport": worker.transport_kind if worker else None,
                "active_generation": worker.generation if worker else None,
                "active_worker_pid": worker.process_pid if worker else None,
                "active_manifest_hash": worker.manifest_hash if worker else None,
                "active_git_sha": worker.git_sha if worker else None,
                "active_source_hash": worker.source_hash if worker else None,
                "active_source_dirty": worker.source_dirty if worker else None,
                "active_loaded_at": worker.started_at_epoch if worker else None,
                "reload_in_progress": self.reloading,
                "reload_count": self.reload_count,
                "last_reload_status": self.last_reload_status,
                "last_reload_started_at": self.last_reload_started_at,
                "last_reload_finished_at": self.last_reload_finished_at,
                "last_reload_error": self.last_reload_error,
                "manifest_drift_detected": self.manifest_drift_detected,
                "pending_manifest_hash": self.pending_manifest_hash,
                "reconnect_required": self.reconnect_required,
                "uptime_seconds": round(time.time() - self.shell_started_at_epoch, 3),
                **self._shell_runtime_identity(),
            }
        _append_dev_trace(
            "health_snapshot_timing",
            dirty_ms=dirty_ms,
            hash_ms=hash_ms,
            lock_wait_ms=lock_wait_ms,
            total_ms=round((time.time() - started_at) * 1000, 3),
            worker_present=worker is not None,
        )
        return snapshot

    def _acquire_worker_for_call(self) -> WorkerProcess:
        needs_recovery = False
        with self.condition:
            while self.reloading:
                self.condition.wait()
            worker = self.active_worker
            if worker is None or not worker.is_alive():
                needs_recovery = True
            else:
                self.active_calls += 1
                return worker

        if needs_recovery:
            recovery_snapshot = self.reload_sync(trigger="auto-recover")
            if recovery_snapshot.get("active_worker_pid") is None:
                raise RuntimeError("No active worker available after recovery attempt")

        with self.condition:
            while self.reloading:
                self.condition.wait()
            worker = self.active_worker
            if worker is None:
                raise RuntimeError("Worker recovery did not produce an active worker")
            self.active_calls += 1
            return worker

    def _spawn_worker(self, generation: int, timeout_seconds: int) -> WorkerProcess:
        if self.internal_worker_transport == HOT_WORKER_TRANSPORT_STDIO_PERSISTENT:
            return self._spawn_persistent_stdio_worker(generation, timeout_seconds)
        if self.internal_worker_transport != HOT_WORKER_TRANSPORT_HTTP:
            raise ValueError(f"Unsupported hot worker transport: {self.internal_worker_transport}")

        port = _find_free_port()
        command = [
            sys.executable,
            "-m",
            "windows_mcp.__main__",
            "--internal-worker",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--generation",
            str(generation),
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = _worker_url(port)
        deadline = time.time() + timeout_seconds
        last_error = "worker did not become ready"
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Worker exited early with code {process.returncode}")
            try:
                meta = asyncio.run(_call_worker_json_tool(url, INTERNAL_META_TOOL))
                return WorkerProcess(
                    generation=generation,
                    transport_kind=HOT_WORKER_TRANSPORT_HTTP,
                    process=process,
                    port=port,
                    manifest_hash=meta["manifest_hash"],
                    git_sha=meta.get("git_sha"),
                    source_hash=meta.get("source_hash"),
                    source_dirty=bool(meta.get("source_dirty", False)),
                    started_at_epoch=float(meta["started_at_epoch"]),
                    process_pid=process.pid,
                )
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.25)

        self._stop_process(process)
        raise RuntimeError(f"Worker readiness timed out: {last_error}")

    def _stop_worker(self, worker: WorkerProcess) -> None:
        if worker.stdio_bridge is not None:
            worker.stdio_bridge.close()
            return
        if worker.process is not None:
            self._stop_process(worker.process)

    def _spawn_persistent_stdio_worker(self, generation: int, timeout_seconds: int) -> WorkerProcess:
        bridge = PersistentStdioWorkerBridge(generation)
        try:
            bridge.start(timeout_seconds)
            meta = bridge.call_tool_sync(INTERNAL_META_TOOL, {}, timeout_seconds=timeout_seconds)
            if not isinstance(meta, str):
                raise ValueError(f"Expected JSON text from {INTERNAL_META_TOOL}, got {type(meta)!r}")
            payload = json.loads(meta)
            return WorkerProcess(
                generation=generation,
                transport_kind=HOT_WORKER_TRANSPORT_STDIO_PERSISTENT,
                manifest_hash=payload["manifest_hash"],
                git_sha=payload.get("git_sha"),
                source_hash=payload.get("source_hash"),
                source_dirty=bool(payload.get("source_dirty", False)),
                started_at_epoch=float(payload["started_at_epoch"]),
                process_pid=payload.get("pid"),
                stdio_bridge=bridge,
            )
        except Exception:
            try:
                bridge.close()
            except Exception:
                pass
            raise

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

def build_worker_mcp(
    *,
    generation: int,
    manifest_hash: str,
    git_sha: str | None,
    source_hash: str | None,
    source_dirty: bool,
) -> FastMCP:
    runtime_holder: dict[str, LocalRuntime] = {}

    @asynccontextmanager
    async def lifespan(_app: FastMCP):
        runtime_holder["runtime"] = await create_local_runtime(role="worker", generation=generation)
        try:
            yield
        finally:
            await close_local_runtime(runtime_holder.get("runtime"))

    mcp = FastMCP(name="windows-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)
    register_public_tools(mcp, build_local_invoker(lambda: runtime_holder["runtime"]))

    @mcp.tool(name=INTERNAL_META_TOOL, description="Internal worker metadata.")
    async def worker_meta_tool(ctx: Context = None):
        runtime = runtime_holder.get("runtime")
        payload = {
            "generation": generation,
            "manifest_hash": manifest_hash,
            "git_sha": git_sha,
            "source_hash": source_hash,
            "source_dirty": source_dirty,
            "pid": os.getpid(),
            "started_at_epoch": runtime.started_at_epoch if runtime else time.time(),
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)

    return mcp


def build_shell_mcp(supervisor: WorkerSupervisor) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_app: FastMCP):
        await asyncio.to_thread(supervisor.start_sync)
        supervisor.start_watcher()
        try:
            yield
        finally:
            await asyncio.to_thread(supervisor.stop_sync)

    mcp = FastMCP(name="windows-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)

    def _wait_locally(duration: int) -> str:
        time.sleep(duration)
        return f"Waited for {duration} seconds."

    async def shell_invoker(tool_name: str, arguments: dict, _ctx: Context | None):
        if tool_name == "Wait":
            duration = int(arguments["duration"])
            return await asyncio.to_thread(_wait_locally, duration)
        return await asyncio.to_thread(supervisor.call_tool_sync, tool_name, arguments)

    async def dev_invoker(_tool_name: str, arguments: dict, _ctx: Context | None):
        wait_for_ready = (
            arguments["wait_for_ready"] is True
            or (
                isinstance(arguments["wait_for_ready"], str)
                and arguments["wait_for_ready"].lower() == "true"
            )
        )
        load_latest = (
            arguments.get("load_latest", True) is True
            or (
                isinstance(arguments.get("load_latest", True), str)
                and arguments["load_latest"].lower() == "true"
            )
        )
        _append_dev_trace(
            "dev_invoker_enter",
            mode=arguments["mode"],
            timeout_seconds=arguments["timeout_seconds"],
            wait_for_ready=wait_for_ready,
            name=arguments.get("name"),
            load_latest=load_latest,
        )
        try:
            payload = await asyncio.to_thread(
                supervisor.dev_server_sync,
                mode=arguments["mode"],
                wait_for_ready=wait_for_ready,
                timeout_seconds=arguments["timeout_seconds"],
                name=arguments.get("name"),
                arguments_json=arguments.get("arguments_json", "{}"),
                load_latest=load_latest,
            )
        except Exception as exc:
            _append_dev_trace("dev_invoker_error", error=str(exc))
            raise
        _append_dev_trace("dev_invoker_ok", payload_length=len(str(payload)))
        return payload

    register_public_tools(mcp, shell_invoker)
    register_dev_server_tool(mcp, dev_invoker)
    return mcp
