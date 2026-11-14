"""Stop only service processes recorded by this project's start script."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "logs" / "campus-services.json"


class ProcessInspectionError(RuntimeError):
    """Process state could not be inspected safely."""


def _command(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ProcessInspectionError(f"could not inspect process {pid}: {exc}") from exc
    value = result.stdout.strip()
    if result.returncode == 0 and value:
        return value
    if result.returncode == 1 and not value and not result.stderr.strip():
        return None
    detail = result.stderr.strip() or f"ps exited with status {result.returncode}"
    raise ProcessInspectionError(f"could not inspect process {pid}: {detail}")


def _alive(item: dict[str, Any]) -> bool:
    command = _command(int(item.get("pid", -1)))
    return bool(command and str(item.get("marker", "")) in command)


def main() -> int:
    if not STATE_PATH.exists():
        print("No tracked project services are running.")
        return 0
    items = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    stopped: list[str] = []
    skipped: list[str] = []
    alive: list[dict[str, Any]] = []
    try:
        # Inspect the full journal before signaling anything. If process
        # inspection is blocked, keep the journal intact for a safe retry.
        for item in items:
            pid = int(item.get("pid", -1))
            command = _command(pid)
            if command and str(item.get("marker", "")) in command:
                alive.append(item)
            else:
                skipped.append(str(item.get("name", pid)))

        for item in reversed(alive):
            pid = int(item.get("pid", -1))
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and _alive(item):
                time.sleep(0.2)
            if _alive(item):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                kill_deadline = time.monotonic() + 2
                while time.monotonic() < kill_deadline and _alive(item):
                    time.sleep(0.1)
                if _alive(item):
                    raise RuntimeError(f"project process {pid} did not exit after SIGKILL")
            stopped.append(str(item.get("name", pid)))
    except (ProcessInspectionError, OSError, RuntimeError) as exc:
        print(f"Could not safely stop project services: {exc}. Process journal retained at {STATE_PATH}.")
        return 2
    STATE_PATH.unlink(missing_ok=True)
    print("Stopped project processes: " + (", ".join(stopped) if stopped else "none"))
    if skipped:
        print("Skipped process IDs that no longer matched the recorded command: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
