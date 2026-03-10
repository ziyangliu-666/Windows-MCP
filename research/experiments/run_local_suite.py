from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run_probe(name: str, args: list[str]) -> dict:
    started = time.time()
    result = subprocess.run(
        [str(PYTHON), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    duration = round(time.time() - started, 2)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    payload = None
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            payload = json.loads(last_line)
        except json.JSONDecodeError:
            payload = {"raw_stdout": stdout}
    return {
        "name": name,
        "returncode": result.returncode,
        "duration_seconds": duration,
        "payload": payload,
        "stderr": stderr,
    }


def main() -> int:
    probes = [
        (
            "notepad_type",
            ["research/experiments/notepad_type_probe.py"],
        ),
        (
            "calculator_verify",
            ["research/experiments/calculator_verify_probe.py"],
        ),
        (
            "launch_notepad_localized",
            ["research/experiments/app_focus_probe.py", "--mode", "launch", "--name", "记事本"],
        ),
        (
            "switch_notepad",
            ["research/experiments/app_focus_probe.py", "--mode", "switch", "--name", "Notepad"],
        ),
    ]

    results = [run_probe(name, args) for name, args in probes]
    success = all(result["returncode"] == 0 for result in results)
    print(
        json.dumps(
            {
                "status": "ok" if success else "failure",
                "timestamp": int(time.time()),
                "results": results,
            },
            ensure_ascii=True,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
