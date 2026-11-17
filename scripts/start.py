"""Start only this project's local Blackboard, agents, controller, and UI."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from campus_blackboard.config import get_settings


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "logs"
STATE_PATH = RUNTIME_DIR / "campus-services.json"
UI_PORT = 8501


class ProcessInspectionError(RuntimeError):
    """Process state could not be inspected safely."""


def _process_command(pid: int) -> str | None:
    try:
        response = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ProcessInspectionError(f"could not inspect process {pid}: {exc}") from exc
    value = response.stdout.strip()
    if response.returncode == 0 and value:
        return value
    if response.returncode == 1 and not value and not response.stderr.strip():
        return None
    detail = response.stderr.strip() or f"ps exited with status {response.returncode}"
    raise ProcessInspectionError(f"could not inspect process {pid}: {detail}")


def _managed_alive(item: dict[str, Any]) -> bool:
    pid = int(item.get("pid", -1))
    command = _process_command(pid)
    return bool(command and str(item.get("marker", "")) in command)


def _stop_items(items: list[dict[str, Any]]) -> None:
    for item in reversed(items):
        pid = int(item.get("pid", -1))
        if not _managed_alive(item):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _managed_alive(item):
            time.sleep(0.2)
        if _managed_alive(item):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and _managed_alive(item):
                time.sleep(0.1)
            if _managed_alive(item):
                raise RuntimeError(f"project process {pid} did not exit after SIGKILL")


def _write_state(items: list[dict[str, Any]]) -> None:
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def _check_credentials(settings: Any) -> None:
    required = {"controller", "dashboard", "evidence", "diagnosis", "review", "response"}
    missing = sorted(role for role in required if not settings.agent_credentials.get(role))
    placeholders = sorted(
        role for role in required
        if str(settings.agent_credentials.get(role, "")).lower().startswith(("replace-", "change-me", "your-"))
    )
    if missing or placeholders:
        raise SystemExit("Set non-placeholder local tokens for the configured service roles in .env before starting.")


def _wait_url(url: str, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _assert_ports_available(bind_host: str, services: list[tuple[str, list[str], str, str]]) -> None:
    """Fail before launch if another process already owns a service port."""
    ports: set[int] = set()
    for name, _, _, url in services:
        parsed = urlparse(url)
        if parsed.port is None:
            raise RuntimeError(f"{name} readiness URL has no port: {url}")
        if parsed.port in ports:
            continue
        ports.add(parsed.port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((bind_host, parsed.port))
        except OSError as exc:
            raise RuntimeError(f"{name} port {parsed.port} on {bind_host} is unavailable: {exc}") from exc


def _verify_started_processes(processes: list[dict[str, Any]]) -> None:
    """Ensure the Popen children survived after readiness URLs responded."""
    for item in processes:
        if not _managed_alive(item):
            raise RuntimeError(f"{item.get('name', 'project')} process exited before startup verification completed")


def main() -> int:
    if not (ROOT / ".env").exists():
        raise SystemExit("Copy .env.example to .env and set local service tokens before starting.")
    settings = get_settings()
    _check_credentials(settings)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    if STATE_PATH.exists():
        existing = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        try:
            active = [item for item in existing if _managed_alive(item)]
        except ProcessInspectionError as exc:
            raise SystemExit(
                f"Cannot safely inspect the existing process journal: {exc}. "
                f"Journal preserved at {STATE_PATH}."
            ) from exc
        if active:
            names = ", ".join(item["name"] for item in active)
            raise SystemExit(f"Project services already appear to be running: {names}. Use scripts/stop.py first.")

    settings.database_path and Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    processes: list[dict[str, Any]] = []
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"

    def start(name: str, command: list[str], marker: str) -> None:
        log_path = RUNTIME_DIR / f"{name}.log"
        stream = log_path.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            stream.close()
        processes.append({
            "name": name,
            "pid": process.pid,
            "marker": marker,
            "log": str(log_path),
            "cwd": str(ROOT),
            "executable": os.path.realpath(python),
        })
        _write_state(processes)

    python = sys.executable
    services = [
        (
            "blackboard",
            [python, "-m", "uvicorn", "campus_blackboard.blackboard.server:app", "--host", settings.bind_host, "--port", str(settings.blackboard_port)],
            "campus_blackboard.blackboard.server:app",
            f"http://{settings.bind_host}:{settings.blackboard_port}/.well-known/agent-card.json",
        )
    ]
    for role in ("evidence", "diagnosis", "review", "response"):
        endpoint = settings.agent_urls.get(role)
        if not endpoint:
            raise SystemExit(f"CAMPUS_{role.upper()}_URL is missing from .env")
        parsed_endpoint = urlparse(endpoint)
        if not parsed_endpoint.port:
            raise SystemExit(f"CAMPUS_{role.upper()}_URL must include a port")
        app_factory = f"campus_blackboard.agents.server:create_{role}_app"
        services.append(
            (
                role,
                [python, "-m", "uvicorn", app_factory, "--factory", "--host", settings.bind_host, "--port", str(parsed_endpoint.port)],
                app_factory,
                f"{endpoint}/.well-known/agent-card.json",
            )
        )
    services.extend(
        [
            (
                "controller",
                [python, "-m", "uvicorn", "campus_blackboard.controller.service:app", "--host", settings.bind_host, "--port", str(settings.controller_port)],
                "campus_blackboard.controller.service:app",
                f"http://{settings.bind_host}:{settings.controller_port}/health",
            ),
            (
                "dashboard",
                [python, "-m", "streamlit", "run", "src/campus_blackboard/dashboard.py", "--server.headless=true", "--server.address", settings.bind_host, "--server.port", str(UI_PORT)],
                "streamlit run src/campus_blackboard/dashboard.py",
                f"http://{settings.bind_host}:{UI_PORT}/_stcore/health",
            ),
        ]
    )

    try:
        _assert_ports_available(settings.bind_host, services)
        for name, command, marker, _ in services:
            start(name, command, marker)
            time.sleep(0.15)
            if not _managed_alive(processes[-1]):
                raise RuntimeError(f"{name} exited during startup; see {processes[-1]['log']}")
        for name, _, _, url in services:
            if not _wait_url(url):
                raise RuntimeError(f"{name} did not become ready at {url}; see {RUNTIME_DIR / (name + '.log')}")
        _verify_started_processes(processes)
    except Exception as exc:
        try:
            if processes:
                _stop_items(processes)
                STATE_PATH.unlink(missing_ok=True)
        except Exception as cleanup_error:
            raise SystemExit(
                f"Startup failed: {exc}. Cleanup could not safely verify all project processes: {cleanup_error}; "
                f"process journal retained at {STATE_PATH}."
            ) from exc
        raise SystemExit(f"Startup failed: {exc}") from exc

    print(f"Started {len(processes)} project processes.")
    print(f"Dashboard: http://{settings.bind_host}:{UI_PORT}")
    print(f"Controller API: http://{settings.bind_host}:{settings.controller_port}")
    if settings.model_mode == "live":
        print(f"Model provider: {settings.model_provider}")
    print(f"Model mode: {settings.model_mode}{' (fixture-driven, no live provider call)' if settings.model_mode == 'offline' else ''}")
    print(f"Logs and process IDs: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
