import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from campus_blackboard.blackboard.client import BlackboardActionError, BlackboardClient
from campus_blackboard.transport.client import call_agent


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _launch(module: str, port: int, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_card(process: subprocess.Popen, url: str) -> dict:
    deadline = time.monotonic() + 10
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("A2A test service exited before Agent Card discovery")
        try:
            response = httpx.get(f"{url}/.well-known/agent-card.json", timeout=0.5)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"A2A Agent Card was not available: {last_error}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.integration
def test_separate_process_discovers_blackboard_and_roundtrips_persistent_data(tmp_path):
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    credentials = {
        "controller": "integration-controller-token",
        "evidence": "integration-evidence-token",
        "dashboard": "integration-dashboard-token",
    }
    env = os.environ.copy()
    env.update(
        {
            "CAMPUS_ENV": "test",
            "CAMPUS_DATABASE_PATH": str(tmp_path / "persistent.sqlite3"),
            "CAMPUS_BIND_HOST": "127.0.0.1",
            "CAMPUS_BLACKBOARD_PORT": str(port),
            "CAMPUS_BLACKBOARD_URL": url,
            "CAMPUS_AGENT_CREDENTIALS_JSON": json.dumps(credentials),
            "CAMPUS_MODEL_MODE": "offline",
        }
    )
    process = _launch("campus_blackboard.blackboard.server:app", port, env)
    try:
        card = _wait_for_card(process, url)
        assert card["name"] == "Campus Blackboard Service"
        interface = card["supportedInterfaces"][0]
        assert interface["protocolBinding"] == "JSONRPC"
        assert interface["url"] == f"{url}/a2a/jsonrpc/"

        async def run_roundtrip() -> tuple[dict, dict]:
            controller = BlackboardClient(url, "controller", credentials["controller"], timeout=10)
            problem = await controller.call(
                "create_problem",
                request_id="create-a2a-case",
                problem_id="a2a-case-1",
                case_id="db_pool",
                title="Portal latency",
            )
            entry = await controller.call(
                "write_blackboard",
                request_id="write-a2a-observation",
                problem_id=problem["problem_id"],
                knowledge_type="observation",
                data={"source_agent": "spoofed", "tool": "query_metrics", "records": [{"name": "latency", "value": 800}]},
            )
            assert entry["source_agent"] == "controller"
            dashboard = BlackboardClient(url, "dashboard", credentials["dashboard"], timeout=10)
            snapshot = await dashboard.call("read_blackboard", problem_id=problem["problem_id"])
            assert snapshot["entries"][0]["entry_id"] == entry["entry_id"]
            with pytest.raises(BlackboardActionError) as denied:
                await dashboard.call(
                    "write_blackboard",
                    request_id="dashboard-write-denied",
                    problem_id=problem["problem_id"],
                    knowledge_type="observation",
                    data={"records": []},
                )
            assert denied.value.code == "forbidden"
            return problem, snapshot

        problem, before_restart = asyncio.run(run_roundtrip())
    finally:
        _stop(process)

    process = _launch("campus_blackboard.blackboard.server:app", port, env)
    try:
        _wait_for_card(process, url)
        persisted = asyncio.run(
            call_agent(
                url,
                {"action": "read_blackboard", "problem_id": problem["problem_id"]},
                actor="dashboard",
                api_key=credentials["dashboard"],
                timeout=10,
            )
        )
        assert persisted["problem"]["board_revision"] == before_restart["problem"]["board_revision"]
        assert persisted["entries"][0]["source_agent"] == "controller"
        assert persisted["history"][0]["entry_id"] == before_restart["entries"][0]["entry_id"]
    finally:
        _stop(process)


@pytest.mark.integration
def test_sdk_streaming_task_returns_artifact_and_real_a2a_ids():
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["CAMPUS_MODEL_MODE"] = "offline"
    env["A2A_TEST_BASE_URL"] = url
    process = _launch("tests.integration.a2a_task_fixture:app", port, env)
    try:
        card = _wait_for_card(process, url)
        assert card["capabilities"]["streaming"] is True
        result = asyncio.run(
            call_agent(
                url,
                {"purpose": "test", "dispatch_id": "dispatch-from-test"},
                actor="controller",
                api_key="test-controller-token",
                timeout=10,
            )
        )
        assert result["accepted"] is True
        assert result["payload"]["dispatch_id"] == "dispatch-from-test"
        assert result["_a2a"]["task_id"]
        assert result["_a2a"]["context_id"]
        assert result["_a2a"]["state"] == "TASK_STATE_COMPLETED"
    finally:
        _stop(process)

