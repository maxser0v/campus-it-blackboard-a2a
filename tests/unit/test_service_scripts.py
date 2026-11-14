from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campus_blackboard.config import Settings
from scripts import start, stop


def _settings(tmp_path: Path) -> Settings:
    ports = {role: 19100 + index for index, role in enumerate(("blackboard", "controller", "evidence", "diagnosis", "review", "response"))}
    urls = {
        role: f"http://127.0.0.1:{port}"
        for role, port in ports.items()
        if role not in {"blackboard", "controller"}
    }
    credentials = {role: f"local-{role}" for role in ("controller", "dashboard", "evidence", "diagnosis", "review", "response")}
    return Settings(
        database_path=str(tmp_path / "blackboard.sqlite3"),
        blackboard_port=ports["blackboard"],
        controller_port=ports["controller"],
        agent_urls=urls,
        agent_credentials=credentials,
    )


def _bind_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: Any) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text("local test env\n", encoding="utf-8")
    runtime = root / "logs"
    runtime.mkdir()
    state_path = runtime / "campus-services.json"
    monkeypatch.setattr(script, "ROOT", root)
    monkeypatch.setattr(script, "RUNTIME_DIR", runtime, raising=False)
    monkeypatch.setattr(script, "STATE_PATH", state_path)
    return state_path


@pytest.mark.parametrize(
    ("module", "function_name"),
    ((start, "_process_command"), (stop, "_command")),
)
def test_process_probe_returns_none_only_for_successfully_missing_pid(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    function_name: str,
) -> None:
    probe = getattr(module, function_name)

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr=""),
    )
    assert probe(812345) is None

    def permission_denied(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise PermissionError("process table inspection denied")

    monkeypatch.setattr(module.subprocess, "run", permission_denied)
    with pytest.raises(module.ProcessInspectionError):
        probe(812345)

    def ambiguous_result(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 2, stdout="", stderr="ps failed")

    monkeypatch.setattr(module.subprocess, "run", ambiguous_result)
    with pytest.raises(module.ProcessInspectionError):
        probe(812345)


def test_start_preserves_existing_pid_journal_when_process_inspection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = _bind_test_root(monkeypatch, tmp_path, start)
    original = json.dumps([{"name": "blackboard", "pid": 812345, "marker": "blackboard.server"}]).encode()
    state_path.write_bytes(original)
    monkeypatch.setattr(start, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(start, "_managed_alive", lambda item: (_ for _ in ()).throw(start.ProcessInspectionError("ps permission denied")))
    monkeypatch.setattr(start.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not start over an uninspected PID"))

    with pytest.raises(SystemExit, match="Cannot safely inspect.*permission denied.*Journal preserved"):
        start.main()

    assert state_path.read_bytes() == original


def test_stop_aborts_without_erasing_journal_when_process_inspection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = _bind_test_root(monkeypatch, tmp_path, stop)
    original = json.dumps([{"name": "blackboard", "pid": 812346, "marker": "blackboard.server"}]).encode()
    state_path.write_bytes(original)
    monkeypatch.setattr(stop, "_command", lambda pid: (_ for _ in ()).throw(stop.ProcessInspectionError("ps permission denied")))
    monkeypatch.setattr(stop.os, "kill", lambda *args, **kwargs: pytest.fail("must not signal an uninspected PID"))

    assert stop.main() == 2
    assert state_path.read_bytes() == original


def test_port_preflight_fails_before_start_overwriting_existing_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = _bind_test_root(monkeypatch, tmp_path, start)
    original = json.dumps([{"name": "old-service", "pid": 812347, "marker": "old-service"}]).encode()
    state_path.write_bytes(original)
    monkeypatch.setattr(start, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(start, "_managed_alive", lambda item: False)
    monkeypatch.setattr(start, "_assert_ports_available", lambda host, services: (_ for _ in ()).throw(RuntimeError("occupied service port")))
    monkeypatch.setattr(start.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("port conflict must be found before launch"))

    with pytest.raises(SystemExit, match="occupied service port"):
        start.main()

    assert state_path.read_bytes() == original


def test_port_preflight_probes_each_unique_readiness_port_without_binding_real_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound: list[tuple[str, int]] = []

    class ProbeSocket:
        def __enter__(self) -> "ProbeSocket":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def bind(self, address: tuple[str, int]) -> None:
            bound.append(address)
            if address[1] == 19202:
                raise OSError("already bound")

    monkeypatch.setattr(start.socket, "socket", lambda *args, **kwargs: ProbeSocket())
    services = [
        ("blackboard", [], "blackboard", "http://127.0.0.1:19201/card"),
        ("controller", [], "controller", "http://127.0.0.1:19202/health"),
        ("alias", [], "alias", "http://127.0.0.1:19202/other"),
    ]

    with pytest.raises(RuntimeError, match="controller port 19202"):
        start._assert_ports_available("127.0.0.1", services)

    assert bound == [("127.0.0.1", 19201), ("127.0.0.1", 19202)]


def test_successful_health_urls_do_not_hide_a_child_that_exited_before_final_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_path = _bind_test_root(monkeypatch, tmp_path, start)
    settings = _settings(tmp_path)
    monkeypatch.setattr(start, "get_settings", lambda: settings)
    monkeypatch.setattr(start, "_assert_ports_available", lambda host, services: None)
    monkeypatch.setattr(start.time, "sleep", lambda seconds: None)
    ready_urls: list[str] = []
    monkeypatch.setattr(start, "_wait_url", lambda url: (ready_urls.append(url) or True))

    pids = iter(range(812400, 812407))
    monkeypatch.setattr(start.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=next(pids)))
    alive_checks: dict[int, int] = {}

    def alive_once(item: dict[str, Any]) -> bool:
        pid = int(item["pid"])
        alive_checks[pid] = alive_checks.get(pid, 0) + 1
        # Each child is alive at the launch check, then has exited by the final
        # check after the old service has already answered its health URL.
        return alive_checks[pid] == 1

    monkeypatch.setattr(start, "_managed_alive", alive_once)

    with pytest.raises(SystemExit, match="exited before startup verification completed"):
        start.main()

    assert len(ready_urls) == 7
    assert not state_path.exists()
    assert len(alive_checks) == 7
