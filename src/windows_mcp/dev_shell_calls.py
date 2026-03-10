from __future__ import annotations

import hashlib
from typing import Any

from windows_mcp.dev_hot import DEV_SHELL_CALLS_PATH, ROOT, SHELL_OWNED_PATHS


def probe_dynamic_reload(_supervisor, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "message": "Dynamic dev shell call executed from disk.",
        "arguments": arguments,
    }


def _hash_shell_paths() -> str:
    digest = hashlib.sha256()
    for path in sorted(SHELL_OWNED_PATHS):
        absolute_path = ROOT / path
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(absolute_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _shell_runtime_identity(supervisor) -> dict[str, Any]:
    identity = {
        "shell_pid": getattr(supervisor, "shell_pid", None) or None,
        "shell_session_id": getattr(supervisor, "shell_session_id", None),
        "shell_started_at_epoch": getattr(supervisor, "shell_started_at_epoch", None),
        "shell_started_at_iso": getattr(supervisor, "shell_started_at_iso", None),
        "shell_python_executable": getattr(supervisor, "shell_python_executable", None),
    }
    if not identity["shell_pid"]:
        try:
            import os

            identity["shell_pid"] = os.getpid()
        except Exception:
            identity["shell_pid"] = None
    if identity["shell_python_executable"] is None:
        try:
            import sys

            identity["shell_python_executable"] = sys.executable
        except Exception:
            identity["shell_python_executable"] = None
    if identity["shell_started_at_iso"] is None and identity["shell_started_at_epoch"]:
        try:
            import time

            identity["shell_started_at_iso"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(float(identity["shell_started_at_epoch"])),
            )
        except Exception:
            identity["shell_started_at_iso"] = None
    if identity["shell_session_id"] is None and identity["shell_pid"] and identity["shell_started_at_epoch"]:
        try:
            identity["shell_session_id"] = (
                f"{identity['shell_pid']}-{int(float(identity['shell_started_at_epoch']) * 1000)}"
            )
        except Exception:
            identity["shell_session_id"] = None
    return identity


def describe_shell_file_delta(supervisor, arguments: dict[str, Any]) -> dict[str, Any]:
    current_file_hashes = {
        path.as_posix(): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in sorted(SHELL_OWNED_PATHS)
    }
    loaded_file_hashes = getattr(supervisor, "shell_loaded_file_hashes", None)
    fallback_mode = "loaded_hashes"
    if loaded_file_hashes is not None:
        loaded_file_hashes = dict(sorted(loaded_file_hashes.items()))
        changed_shell_paths = [
            path
            for path, current_hash in sorted(current_file_hashes.items())
            if loaded_file_hashes.get(path) != current_hash
        ]
    else:
        fallback_mode = "mtime_since_shell_start"
        shell_started_at_epoch = float(getattr(supervisor, "shell_started_at_epoch", 0.0) or 0.0)
        changed_shell_paths = [
            path.as_posix()
            for path in sorted(SHELL_OWNED_PATHS)
            if (ROOT / path).stat().st_mtime > shell_started_at_epoch
        ]
        loaded_file_hashes = None
    loaded_source_hash = getattr(supervisor, "shell_loaded_source_hash", None)
    current_source_hash = _hash_shell_paths()
    return {
        "dynamic_module_path": DEV_SHELL_CALLS_PATH.as_posix(),
        "shell_owned_paths": [path.as_posix() for path in SHELL_OWNED_PATHS],
        "shell_loaded_file_hashes": loaded_file_hashes,
        "shell_current_file_hashes": dict(sorted(current_file_hashes.items())),
        "shell_loaded_source_hash": loaded_source_hash,
        "shell_current_source_hash": current_source_hash,
        "shell_restart_required": (
            loaded_source_hash != current_source_hash
            if loaded_source_hash is not None
            else bool(changed_shell_paths)
        ),
        "reconnect_required": getattr(supervisor, "reconnect_required", None),
        "changed_shell_paths": changed_shell_paths,
        "fallback_mode": fallback_mode,
        "requested_arguments": arguments,
        **_shell_runtime_identity(supervisor),
    }


CALLS = {
    "probe_dynamic_reload": {
        "callable": probe_dynamic_reload,
        "description": "Minimal dynamic call used to verify that the shell can load dev checks from disk without restart.",
    },
    "describe_shell_file_delta": {
        "callable": describe_shell_file_delta,
        "description": "Report which shell-owned files differ from the hashes loaded by the current shell process.",
    },
}
