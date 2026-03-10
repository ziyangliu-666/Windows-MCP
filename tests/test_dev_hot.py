from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastmcp import Client

from windows_mcp import dev_shell_calls
from windows_mcp.server_core import build_local_mcp, get_public_tool_names
from windows_mcp.dev_hot import (
    HOT_WORKER_TRANSPORT_HTTP,
    HOT_WORKER_TRANSPORT_STDIO_PERSISTENT,
    WorkerProcess,
    WorkerSupervisor,
    build_shell_mcp,
)


class FakeProcess:
    _pid = 1000

    def __init__(self, *, alive: bool = True):
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.returncode = None if alive else 1
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FakeSupervisor:
    def start_sync(self):
        return {"status": "ok"}

    def stop_sync(self):
        return None

    def start_watcher(self):
        return None

    def call_tool_sync(self, tool_name: str, arguments: dict):
        return {"tool_name": tool_name, "arguments": arguments}

    def dev_server_sync(
        self,
        *,
        mode: str,
        wait_for_ready: bool = True,
        timeout_seconds: int = 15,
        name: str | None = None,
        arguments_json: str = "{}",
        load_latest: bool = True,
    ):
        return f"{mode}:{wait_for_ready}:{timeout_seconds}:{name}:{arguments_json}:{load_latest}"


class FakeBridge:
    def __init__(self, *, alive: bool = True):
        self.alive = alive
        self.closed = False
        self.calls: list[tuple[str, dict, int]] = []

    def call_tool_sync(self, tool_name: str, arguments: dict, timeout_seconds: int):
        self.calls.append((tool_name, arguments, timeout_seconds))
        return f"bridge:{tool_name}"

    def close(self, timeout_seconds: int = 10):
        self.closed = True

    def is_alive(self) -> bool:
        return self.alive and not self.closed


def make_worker(
    generation: int,
    manifest_hash: str,
    *,
    transport_kind: str = HOT_WORKER_TRANSPORT_HTTP,
    bridge: FakeBridge | None = None,
) -> WorkerProcess:
    return WorkerProcess(
        generation=generation,
        transport_kind=transport_kind,
        process=FakeProcess() if transport_kind == HOT_WORKER_TRANSPORT_HTTP else None,
        port=(8000 + generation) if transport_kind == HOT_WORKER_TRANSPORT_HTTP else None,
        process_pid=9000 + generation,
        manifest_hash=manifest_hash,
        git_sha="abc123",
        source_hash=f"source-{generation}",
        source_dirty=(generation % 2 == 0),
        started_at_epoch=float(generation),
        stdio_bridge=bridge,
    )


def test_build_shell_mcp_adds_devserver_tool():
    shell = build_shell_mcp(FakeSupervisor())

    async def run():
        async with Client(shell) as client:
            tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert "DevServer" in names
        assert set(get_public_tool_names()).issubset(names)

    asyncio.run(run())


def test_local_mcp_does_not_expose_devserver():
    local_mcp = build_local_mcp()

    async def run():
        async with Client(local_mcp) as client:
            tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert "DevServer" not in names
        assert names == set(get_public_tool_names())

    asyncio.run(run())


def test_shell_wait_tool_bypasses_worker_forwarding():
    class WaitFailSupervisor(FakeSupervisor):
        def call_tool_sync(self, tool_name: str, arguments: dict):
            if tool_name == "Wait":
                raise AssertionError("Wait should not be forwarded to the worker")
            return super().call_tool_sync(tool_name, arguments)

    shell = build_shell_mcp(WaitFailSupervisor())

    async def run():
        async with Client(shell) as client:
            result = await client.call_tool("Wait", {"duration": 0}, raise_on_error=False)
        payload = [block.model_dump() for block in result.content]
        assert payload[0]["text"] == "Waited for 0 seconds."

    asyncio.run(run())


def test_shell_devserver_tool_forwards_dynamic_call_arguments():
    shell = build_shell_mcp(FakeSupervisor())

    async def run():
        async with Client(shell) as client:
            result = await client.call_tool(
                "DevServer",
                {
                    "mode": "call",
                    "name": "probe_dynamic_reload",
                    "arguments_json": '{"value": 7}',
                    "load_latest": True,
                    "timeout_seconds": 9,
                },
                raise_on_error=False,
            )
        payload = [block.model_dump() for block in result.content]
        assert payload[0]["text"] == (
            'call:True:9:probe_dynamic_reload:{"value": 7}:True'
        )

    asyncio.run(run())


def test_dev_server_sync_writes_trace(monkeypatch, tmp_path):
    trace_file = tmp_path / "devserver_trace.jsonl"
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    worker = make_worker(1, "expected")

    monkeypatch.setenv("WMCP_DEV_TRACE_FILE", str(trace_file))
    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-1")
    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_dirty", lambda: False)
    monkeypatch.setattr(supervisor, "_spawn_worker", lambda generation, timeout_seconds: worker)

    supervisor.start_sync()
    payload = supervisor.dev_server_sync(mode="health", wait_for_ready=True, timeout_seconds=7)

    assert '"active_generation": 1' in payload
    records = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
    events = [record["event"] for record in records]
    assert "dev_server_sync_enter" in events
    assert "health_snapshot_timing" in events
    assert "dev_server_sync_health_ok" in events


def test_dev_server_call_lists_native_and_dynamic_calls(monkeypatch, tmp_path):
    dynamic_module = tmp_path / "dev_shell_calls.py"
    dynamic_module.write_text(
        "\n".join(
            [
                "def ping(supervisor, arguments):",
                "    return {'ok': True, 'arguments': arguments}",
                "",
                "CALLS = {",
                "    'ping': {",
                "        'callable': ping,",
                "        'description': 'Dynamic ping call',",
                "    }",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    monkeypatch.setattr("windows_mcp.dev_hot.DEV_SHELL_CALLS_PATH", dynamic_module)
    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-1")

    payload = json.loads(
        supervisor.dev_server_sync(
            mode="call",
            name="list_calls",
            arguments_json="{}",
            load_latest=True,
        )
    )

    assert payload["name"] == "list_calls"
    assert payload["source"] == "native"
    calls = {entry["name"]: entry for entry in payload["result"]["calls"]}
    assert "list_calls" in calls
    assert "describe_restart_boundary" in calls
    assert "describe_shell_file_delta" in calls
    assert calls["ping"]["source"] == "dynamic"


def test_dev_server_call_loads_dynamic_module_from_disk_without_restart(monkeypatch, tmp_path):
    dynamic_module = tmp_path / "dev_shell_calls.py"
    dynamic_module.write_text(
        "\n".join(
            [
                "def probe(supervisor, arguments):",
                "    return {'version': 1, 'arguments': arguments}",
                "",
                "CALLS = {'probe': probe}",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    monkeypatch.setattr("windows_mcp.dev_hot.DEV_SHELL_CALLS_PATH", dynamic_module)

    first = json.loads(
        supervisor.dev_server_sync(
            mode="call",
            name="probe",
            arguments_json='{\"value\": 1}',
            load_latest=True,
        )
    )

    dynamic_module.write_text(
        "\n".join(
            [
                "def probe(supervisor, arguments):",
                "    return {'version': 2, 'arguments': arguments}",
                "",
                "CALLS = {'probe': probe}",
            ]
        ),
        encoding="utf-8",
    )

    second = json.loads(
        supervisor.dev_server_sync(
            mode="call",
            name="probe",
            arguments_json='{\"value\": 2}',
            load_latest=True,
        )
    )

    assert first["source"] == "dynamic"
    assert first["result"]["version"] == 1
    assert first["result"]["arguments"] == {"value": 1}
    assert second["source"] == "dynamic"
    assert second["result"]["version"] == 2
    assert second["result"]["arguments"] == {"value": 2}


def test_describe_restart_boundary_reports_changed_shell_paths(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    supervisor.shell_loaded_source_hash = "shell-hash-1"
    supervisor.shell_loaded_file_hashes = {
        "src/windows_mcp/__main__.py": "same",
        "src/windows_mcp/dev_hot.py": "old",
        "src/windows_mcp/server_core.py": "same",
    }

    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-2")
    monkeypatch.setattr(
        "windows_mcp.dev_hot.get_shell_code_file_hashes",
        lambda: {
            "src/windows_mcp/__main__.py": "same",
            "src/windows_mcp/dev_hot.py": "new",
            "src/windows_mcp/server_core.py": "same",
        },
    )

    payload = json.loads(
        supervisor.dev_server_sync(
            mode="call",
            name="describe_restart_boundary",
            arguments_json="{}",
            load_latest=True,
        )
    )

    assert payload["source"] == "native"
    assert payload["result"]["shell_restart_required"] is True
    assert payload["result"]["changed_shell_paths"] == ["src/windows_mcp/dev_hot.py"]


def test_native_describe_shell_file_delta_reports_loaded_hash_changes(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    supervisor.shell_loaded_file_hashes = {
        "src/windows_mcp/__main__.py": "same",
        "src/windows_mcp/dev_hot.py": "same",
        "src/windows_mcp/server_core.py": "old",
    }

    monkeypatch.setattr(
        "windows_mcp.dev_hot.get_shell_code_file_hashes",
        lambda: {
            "src/windows_mcp/__main__.py": "same",
            "src/windows_mcp/dev_hot.py": "same",
            "src/windows_mcp/server_core.py": "new",
        },
    )

    payload = json.loads(
        supervisor.dev_server_sync(
            mode="call",
            name="describe_shell_file_delta",
            arguments_json="{}",
            load_latest=True,
        )
    )

    assert payload["source"] == "native"
    assert payload["result"]["fallback_mode"] == "loaded_hashes"
    assert payload["result"]["changed_shell_paths"] == ["src/windows_mcp/server_core.py"]
    assert payload["result"]["shell_pid"] > 0
    assert payload["result"]["shell_session_id"]
    assert payload["result"]["shell_started_at_iso"].endswith("Z")


def test_supervisor_reload_swaps_compatible_worker(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    first = make_worker(1, "expected")
    second = make_worker(2, "expected")
    spawned = [first, second]
    stopped: list[int] = []

    monkeypatch.setattr(
        supervisor,
        "_spawn_worker",
        lambda generation, timeout_seconds: spawned.pop(0),
    )
    monkeypatch.setattr(
        supervisor,
        "_stop_worker",
        lambda worker: stopped.append(worker.generation),
    )

    supervisor.start_sync()
    snapshot = supervisor.reload_sync(trigger="test")

    assert snapshot["reconnect_required"] is False
    assert snapshot["active_generation"] == 2
    assert supervisor.active_worker is second
    assert stopped == [1]


def test_supervisor_rejects_manifest_drift(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    first = make_worker(1, "expected")
    incompatible = make_worker(2, "different")
    spawned = [first, incompatible]
    stopped: list[int] = []

    monkeypatch.setattr(
        supervisor,
        "_spawn_worker",
        lambda generation, timeout_seconds: spawned.pop(0),
    )
    monkeypatch.setattr(
        supervisor,
        "_stop_worker",
        lambda worker: stopped.append(worker.generation),
    )

    supervisor.start_sync()
    snapshot = supervisor.reload_sync(trigger="test")

    assert snapshot["manifest_drift_detected"] is True
    assert snapshot["reconnect_required"] is True
    assert snapshot["active_generation"] is None
    assert supervisor.active_worker is None
    assert stopped == [1, 2]


def test_supervisor_clears_active_worker_when_spawn_fails(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    first = make_worker(1, "expected")
    stopped: list[int] = []

    def fake_spawn(generation, timeout_seconds):
        if generation == 1:
            return first
        raise RuntimeError("boom")

    monkeypatch.setattr(supervisor, "_spawn_worker", fake_spawn)
    monkeypatch.setattr(
        supervisor,
        "_stop_worker",
        lambda worker: stopped.append(worker.generation),
    )

    supervisor.start_sync()
    snapshot = supervisor.reload_sync(trigger="test")

    assert snapshot["active_generation"] is None
    assert snapshot["last_reload_status"] == "failed"
    assert "boom" in snapshot["last_reload_error"]
    assert supervisor.active_worker is None
    assert stopped == [1]


def test_health_snapshot_exposes_source_identity(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    worker = make_worker(1, "expected")

    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-1")
    monkeypatch.setattr(
        supervisor,
        "_spawn_worker",
        lambda generation, timeout_seconds: worker,
    )

    supervisor.shell_loaded_source_hash = "shell-hash-1"
    supervisor.shell_source_dirty = False
    supervisor.shell_source_dirty_check = "ok"
    snapshot = supervisor.start_sync()

    assert snapshot["shell_loaded_source_hash"] == "shell-hash-1"
    assert snapshot["shell_current_source_hash"] == "shell-hash-1"
    assert snapshot["shell_source_dirty"] is False
    assert snapshot["shell_restart_required"] is False
    assert snapshot["active_git_sha"] == "abc123"
    assert snapshot["active_source_hash"] == "source-1"
    assert snapshot["active_source_dirty"] is False
    assert snapshot["shell_pid"] > 0
    assert snapshot["shell_session_id"]
    assert snapshot["shell_started_at_epoch"] > 0
    assert snapshot["shell_started_at_iso"].endswith("Z")
    assert snapshot["shell_python_executable"]


def test_dynamic_describe_shell_file_delta_fallback_exposes_runtime_identity(monkeypatch):
    supervisor = SimpleNamespace(
        shell_session_id="pid-start",
        shell_started_at_epoch=123.0,
        shell_loaded_source_hash="loaded-hash",
        reconnect_required=False,
    )

    monkeypatch.setattr(
        dev_shell_calls,
        "SHELL_OWNED_PATHS",
        (dev_shell_calls.SHELL_OWNED_PATHS[0],),
    )

    payload = dev_shell_calls.describe_shell_file_delta(supervisor, {})

    assert payload["shell_session_id"] == "pid-start"
    assert payload["shell_started_at_epoch"] == 123.0
    assert payload["shell_started_at_iso"] == "1970-01-01T00:02:03Z"
    assert payload["shell_pid"] > 0
    assert payload["shell_python_executable"]
    assert payload["fallback_mode"] == "mtime_since_shell_start"
    assert payload["shell_loaded_source_hash"] == "loaded-hash"
    assert payload["shell_current_source_hash"]
    assert payload["shell_restart_required"] is True
    assert payload["reconnect_required"] is False


def test_health_snapshot_flags_stale_shell_code(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")

    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-2")

    supervisor.shell_loaded_source_hash = "shell-hash-1"
    supervisor.shell_source_dirty = True
    supervisor.shell_source_dirty_check = "ok"
    snapshot = supervisor.health_snapshot()

    assert snapshot["shell_loaded_source_hash"] == "shell-hash-1"
    assert snapshot["shell_current_source_hash"] == "shell-hash-2"
    assert snapshot["shell_source_dirty"] is True
    assert snapshot["shell_restart_required"] is True


def test_health_snapshot_defaults_dirty_state_to_disabled(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")

    monkeypatch.setattr("windows_mcp.dev_hot.get_shell_code_hash", lambda: "shell-hash-1")

    supervisor.shell_loaded_source_hash = "shell-hash-1"
    snapshot = supervisor.health_snapshot()

    assert snapshot["shell_source_dirty"] is None
    assert snapshot["shell_source_dirty_check"] == "disabled"


def test_supervisor_uses_persistent_stdio_bridge(monkeypatch):
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    bridge = FakeBridge()
    worker = make_worker(
        1,
        "expected",
        transport_kind=HOT_WORKER_TRANSPORT_STDIO_PERSISTENT,
        bridge=bridge,
    )
    supervisor.active_worker = worker
    supervisor.active_calls = 1

    result = supervisor.call_tool_sync("Clipboard", {"mode": "get"})

    assert result == "bridge:Clipboard"
    assert bridge.calls == [("Clipboard", {"mode": "get"}, 120)]


def test_supervisor_stop_closes_persistent_stdio_bridge():
    supervisor = WorkerSupervisor(expected_manifest_hash="expected")
    bridge = FakeBridge()
    worker = make_worker(
        1,
        "expected",
        transport_kind=HOT_WORKER_TRANSPORT_STDIO_PERSISTENT,
        bridge=bridge,
    )

    supervisor._stop_worker(worker)

    assert bridge.closed is True
