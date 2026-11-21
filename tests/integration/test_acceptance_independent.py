"""Independent end-to-end checks for the locked local implementation.

The service acceptance test starts real Uvicorn processes on isolated ports and
uses a throwaway SQLite database. It never contacts a paid model provider.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from campus_blackboard.blackboard.client import BlackboardClient
from campus_blackboard.blackboard.repository import Repository
from campus_blackboard.config import Settings
from campus_blackboard.model_adapter import ModelAdapterError, OpenAICompatibleAdapter, model_from_settings
from campus_blackboard.tools.fixtures import read_case_summary


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
ROLES = ("evidence", "diagnosis", "review", "response")


def _free_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
        return [int(probe.getsockname()[1]) for probe in sockets]
    finally:
        for probe in sockets:
            probe.close()


def _service_environment(tmp_path: Path, ports: list[int]) -> tuple[dict[str, str], dict[str, str]]:
    blackboard_port, controller_port, *agent_ports = ports
    urls = {
        "blackboard": f"http://127.0.0.1:{blackboard_port}",
        "controller": f"http://127.0.0.1:{controller_port}",
        **{
            role: f"http://127.0.0.1:{port}"
            for role, port in zip(ROLES, agent_ports, strict=True)
        },
    }
    credentials = {role: f"acceptance-{uuid.uuid4().hex}" for role in (*urls, "dashboard")}
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("CAMPUS_") or name.startswith("MODEL_"):
            environment.pop(name)
    environment.update(
        {
            "CAMPUS_ENV": "local",
            "CAMPUS_BIND_HOST": "127.0.0.1",
            "CAMPUS_DATABASE_PATH": str(tmp_path / "acceptance.sqlite3"),
            "CAMPUS_BLACKBOARD_PORT": str(blackboard_port),
            "CAMPUS_CONTROLLER_PORT": str(controller_port),
            "CAMPUS_BLACKBOARD_URL": urls["blackboard"],
            "CAMPUS_CONTROLLER_URL": urls["controller"],
            "CAMPUS_EVIDENCE_URL": urls["evidence"],
            "CAMPUS_DIAGNOSIS_URL": urls["diagnosis"],
            "CAMPUS_REVIEW_URL": urls["review"],
            "CAMPUS_RESPONSE_URL": urls["response"],
            "CAMPUS_AGENT_CREDENTIALS_JSON": json.dumps(credentials),
            "CAMPUS_MODEL_MODE": "offline",
            "CAMPUS_REQUEST_TIMEOUT": "15",
            "CAMPUS_CASE_TIMEOUT_SECONDS": "45",
            "CAMPUS_MAX_DISPATCHES": "12",
        }
    )
    return environment, urls | {"credentials": credentials}


class _ServiceDetails(dict[str, Any]):
    """Keep generated bearer credentials out of pytest failure representations."""

    def __repr__(self) -> str:
        safe = {key: ("<redacted>" if key == "credentials" else value) for key, value in self.items()}
        return f"_ServiceDetails({safe!r})"


@pytest.fixture(scope="module")
def local_services(tmp_path_factory: pytest.TempPathFactory):
    """Start the Blackboard, four agents, and controller on unique ports."""
    tmp_path = tmp_path_factory.mktemp("a2a-acceptance")
    ports = _free_ports(6)
    environment, details = _service_environment(tmp_path, ports)
    processes: list[subprocess.Popen[bytes]] = []
    log_paths: list[Path] = []
    commands = [
        [
            PYTHON,
            "-m",
            "uvicorn",
            "campus_blackboard.blackboard.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(ports[0]),
        ],
        *[
            [
                PYTHON,
                "-m",
                "uvicorn",
                f"campus_blackboard.agents.server:create_{role}_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[2 + index]),
            ]
            for index, role in enumerate(ROLES)
        ],
        [
            PYTHON,
            "-m",
            "uvicorn",
            "campus_blackboard.controller.service:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(ports[1]),
        ],
    ]
    try:
        for index, command in enumerate(commands):
            log_path = tmp_path / f"service-{index}.log"
            log_paths.append(log_path)
            with log_path.open("wb") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            processes.append(process)

        expected = [
            f"{details[role]}/.well-known/agent-card.json"
            for role in ("blackboard", *ROLES)
        ] + [f"{details['controller']}/health"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                break
            try:
                if all(httpx.get(url, timeout=0.4).status_code == 200 for url in expected):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("local services did not become ready")

        if any(process.poll() is not None for process in processes):
            logs = "\n".join(path.read_text(errors="replace")[-4000:] for path in log_paths)
            raise AssertionError(f"a local service exited during startup:\n{logs}")
        if not all(httpx.get(url, timeout=2).status_code == 200 for url in expected):
            logs = "\n".join(path.read_text(errors="replace")[-4000:] for path in log_paths)
            raise AssertionError(f"local service readiness timed out:\n{logs}")
        yield _ServiceDetails(details)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_sqlite_persistence_actor_revision_concurrency_and_idempotency(tmp_path: Path) -> None:
    database = tmp_path / "blackboard.sqlite3"
    board = Repository(database)
    created = board.handle(
        "controller",
        {
            "action": "create_problem",
            "request_id": "create-acceptance-problem",
            "case_id": "db_pool",
            "title": "Acceptance case",
        },
    )
    problem_id = created["problem_id"]
    write_request = {
        "action": "write_blackboard",
        "request_id": "write-once",
        "problem_id": problem_id,
        "knowledge_type": "hypothesis",
        "data": {"claim": "First revision", "source_agent": "spoofed-review"},
    }
    first = board.handle("diagnosis", write_request)
    duplicate = board.handle("diagnosis", write_request)
    changed_payload = {**write_request, "data": {"claim": "Different payload"}}
    collision = board.handle("diagnosis", changed_payload)

    assert first["entry_id"] == duplicate["entry_id"]
    assert duplicate["duplicate"] is True
    assert collision["error"]["code"] == "idempotency_conflict"

    def update(revision_text: str) -> dict[str, Any]:
        return board.handle(
            "diagnosis",
            {
                "action": "update_knowledge",
                "request_id": f"revise-{revision_text}",
                "problem_id": problem_id,
                "entry_id": first["entry_id"],
                "expected_revision": 1,
                "data": {"claim": revision_text},
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(update, ("second-a", "second-b")))
    assert sum("error" not in result for result in concurrent) == 1
    conflict = next(result for result in concurrent if "error" in result)
    assert conflict["error"]["code"] == "revision_conflict"

    read_request = {"action": "read_blackboard", "problem_id": problem_id}
    snapshot = board.handle("dashboard", read_request)
    assert snapshot["entries"][0]["source_agent"] == "diagnosis"
    assert [item["revision"] for item in snapshot["history"] if item["entry_id"] == first["entry_id"]] == [1, 2]
    revision_before_read = snapshot["problem"]["board_revision"]
    repeated_read = board.handle("dashboard", read_request)
    assert repeated_read["problem"]["board_revision"] == revision_before_read
    assert board.handle(
        "dashboard",
        {"action": "write_blackboard", "request_id": "dashboard-write", "problem_id": problem_id, "knowledge_type": "observation", "data": {}},
    )["error"]["code"] == "forbidden"
    assert board.handle(
        "diagnosis",
        {"action": "set_problem_status", "request_id": "agent-status", "problem_id": problem_id, "status": "completed"},
    )["error"]["code"] == "forbidden"
    board.close()

    reopened = Repository(database)
    after_restart = reopened.handle("dashboard", read_request)
    assert after_restart["problem"]["problem_id"] == problem_id
    assert len(after_restart["history"]) == 2
    reopened.close()


def test_concurrent_blackboard_reads_and_writes_keep_read_snapshots_available(tmp_path: Path) -> None:
    board = Repository(tmp_path / "read-write-concurrency.sqlite3")
    created = board.handle(
        "controller",
        {
            "action": "create_problem",
            "request_id": "create-read-write-problem",
            "case_id": "db_pool",
            "title": "Concurrent read/write acceptance case",
        },
    )
    problem_id = created["problem_id"]

    def exercise(index: int) -> dict[str, Any]:
        if index % 4 == 0:
            return board.handle(
                "dashboard",
                {"action": "get_problem_status", "problem_id": problem_id},
            )
        if index % 4 in {1, 2}:
            return board.handle("dashboard", {"action": "read_blackboard", "problem_id": problem_id})
        return board.handle(
            "evidence",
            {
                "action": "write_blackboard",
                "request_id": f"concurrent-observation-{index}",
                "problem_id": problem_id,
                "knowledge_type": "observation",
                "data": {"records": [{"sequence": index}]},
            },
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(exercise, range(120)))
    errors = [result["error"] for result in results if "error" in result]
    assert errors == []

    snapshots = [result for result in results if "problem" in result]
    statuses = [result for result in results if "unresolved_requests" in result]
    assert snapshots
    assert statuses
    for snapshot in snapshots:
        # The problem row and its entries must come from the same SQLite
        # snapshot even while concurrent evidence writes are committed.
        assert snapshot["problem"]["board_revision"] == len(snapshot["entries"])
    assert all(0 <= result["board_revision"] <= 30 for result in statuses)
    final_snapshot = board.handle("dashboard", {"action": "read_blackboard", "problem_id": problem_id})
    assert len(final_snapshot["entries"]) == 30
    assert final_snapshot["problem"]["board_revision"] == 30
    board.close()


def test_problem_status_distinguishes_fulfilled_from_unavailable_evidence(tmp_path: Path) -> None:
    board = Repository(tmp_path / "request-status.sqlite3")
    problem = board.handle(
        "controller",
        {
            "action": "create_problem",
            "request_id": "create-request-status-case",
            "case_id": "missing_evidence",
            "title": "Evidence request state case",
        },
    )
    request = board.handle(
        "review",
        {
            "action": "write_blackboard",
            "request_id": "write-evidence-request",
            "problem_id": problem["problem_id"],
            "knowledge_type": "evidence_request",
            "data": {"question": "What is the upstream error rate?", "status": "open"},
        },
    )
    read_status = {"action": "get_problem_status", "problem_id": problem["problem_id"]}
    assert [row["entry_id"] for row in board.handle("dashboard", read_status)["unresolved_requests"]] == [request["entry_id"]]

    board.handle(
        "evidence",
        {
            "action": "write_blackboard",
            "request_id": "write-unavailable-observation",
            "problem_id": problem["problem_id"],
            "knowledge_type": "observation",
            "data": {
                "records": [],
                "fulfilled_request_entry_id": request["entry_id"],
                "fulfilled_request_status": "unavailable",
            },
        },
    )
    unavailable = board.handle("dashboard", read_status)["unresolved_requests"]
    assert len(unavailable) == 1
    assert unavailable[0]["evidence_status"] == "unavailable"

    board.handle(
        "evidence",
        {
            "action": "write_blackboard",
            "request_id": "write-fulfilled-observation",
            "problem_id": problem["problem_id"],
            "knowledge_type": "observation",
            "data": {
                "records": [{"name": "upstream_error_rate_pct", "value": 0}],
                "fulfilled_request_entry_id": request["entry_id"],
                "fulfilled_request_status": "fulfilled",
            },
        },
    )
    assert board.handle("dashboard", read_status)["unresolved_requests"] == []
    board.close()


def test_real_a2a_discovery_task_ids_and_three_fixture_outcomes(local_services: dict[str, Any]) -> None:
    credentials = local_services["credentials"]
    blackboard_url = local_services["blackboard"]
    controller_url = local_services["controller"]
    health = httpx.get(f"{controller_url}/health", timeout=3).json()
    assert health["orchestrator"] == "langgraph"
    assert health["agent_framework"] == "langchain"

    for role in ("blackboard", *ROLES):
        card_response = httpx.get(f"{local_services[role]}/.well-known/agent-card.json", timeout=3)
        assert card_response.status_code == 200
        card = card_response.json()
        assert card["name"]
        interfaces = card.get("supportedInterfaces", card.get("supported_interfaces", []))
        assert any(
            interface.get("protocolBinding", interface.get("protocol_binding")) == "JSONRPC"
            for interface in interfaces
        )

    unauthenticated = httpx.post(f"{blackboard_url}/a2a/jsonrpc/", json={}, timeout=3)
    assert unauthenticated.status_code == 401

    board = BlackboardClient(blackboard_url, actor="dashboard", api_key=credentials["dashboard"], timeout=15)
    controlled = BlackboardClient(blackboard_url, actor="controller", api_key=credentials["controller"], timeout=15)
    actor_probe = asyncio.run(
        controlled.call(
            "create_problem",
            request_id=f"actor-probe-{uuid.uuid4().hex}",
            case_id="db_pool",
            title="Actor identity probe",
        )
    )
    actor_write = asyncio.run(
        BlackboardClient(blackboard_url, actor="diagnosis", api_key=credentials["diagnosis"], timeout=15).call(
            "write_blackboard",
            request_id=f"actor-write-{uuid.uuid4().hex}",
            problem_id=actor_probe["problem_id"],
            knowledge_type="observation",
            data={"source_agent": "spoofed-review", "records": []},
        )
    )
    actor_snapshot = asyncio.run(board.call("read_blackboard", problem_id=actor_probe["problem_id"]))
    assert actor_snapshot["entries"][0]["source_agent"] == "diagnosis"

    expectations = {
        "db_pool": "completed",
        "dns_fault": "completed",
        "missing_evidence": "unresolved",
    }
    observed: dict[str, dict[str, Any]] = {}
    for case_id, expected_status in expectations.items():
        request_id = f"acceptance-{case_id}-{uuid.uuid4().hex}"
        start_response = httpx.post(
            f"{controller_url}/runs",
            json={"case_id": case_id, "client_request_id": request_id},
            timeout=10,
        )
        assert start_response.status_code == 202, start_response.text
        started = start_response.json()
        replay = httpx.post(
            f"{controller_url}/runs",
            json={"case_id": case_id, "client_request_id": request_id},
            timeout=10,
        )
        assert replay.status_code == 202
        assert replay.json()["problem_id"] == started["problem_id"]

        deadline = time.monotonic() + 60
        while True:
            response = httpx.get(f"{controller_url}/runs/{started['problem_id']}", timeout=10)
            assert response.status_code == 200, response.text
            run = response.json()
            if run["status"] in {"completed", "unresolved", "blocked", "cancelled"} and run.get("result") is not None:
                break
            assert time.monotonic() < deadline, f"case {case_id} did not terminate: {run}"
            time.sleep(0.15)
        assert run["status"] == expected_status, (case_id, run)
        assert run["result"]["orchestrator"] == "langgraph"
        assert run["result"]["workflow_nodes"][0] == "controller"
        assert run["result"]["workflow_nodes"][-1] == "finish"
        snapshot = asyncio.run(board.call("read_blackboard", problem_id=started["problem_id"]))
        entries = snapshot["entries"]
        history = snapshot["history"]
        problem_status = asyncio.run(
            board.call("get_problem_status", problem_id=started["problem_id"])
        )
        dispatch_started = [
            entry for entry in entries
            if entry["knowledge_type"] == "controller_decision"
            and entry["data"].get("event_type") == "dispatch_started"
        ]
        dispatch_finished = [
            entry for entry in entries
            if entry["knowledge_type"] == "controller_decision"
            and entry["data"].get("event_type") == "dispatch_finished"
        ]
        finished_by_id = {entry["data"].get("dispatch_id"): entry["data"] for entry in dispatch_finished}
        assert dispatch_started
        assert all(event["data"]["orchestrator"] == "langgraph" for event in dispatch_started)
        assert all(event["data"]["graph_node"] in ROLES for event in dispatch_started)
        assert len(dispatch_started) <= 12
        for event in dispatch_started:
            finished = finished_by_id.get(event["data"].get("dispatch_id"))
            assert finished is not None
            assert finished.get("task_id"), finished
            assert finished.get("context_id"), finished
            assert finished.get("task_status") == "TASK_STATE_COMPLETED", finished

        hypotheses = [entry for entry in entries if entry["knowledge_type"] == "hypothesis"]
        reviews = [entry for entry in entries if entry["knowledge_type"] == "review"]
        solutions = [entry for entry in entries if entry["knowledge_type"] == "solution"]
        accepted_current = [
            hypothesis for hypothesis in hypotheses
            if hypothesis["data"].get("status") == "supported"
            and any(
                review["data"].get("target_entry_id") == hypothesis["entry_id"]
                and review["data"].get("target_revision") == hypothesis["revision"]
                and review["data"].get("verdict") == "accept"
                for review in reviews
            )
        ]
        if case_id == "db_pool":
            requests = [entry for entry in entries if entry["knowledge_type"] == "evidence_request"]
            observations = [entry for entry in entries if entry["knowledge_type"] == "observation"]
            assert requests
            assert any(
                observation["data"].get("fulfilled_request_entry_id") in {request["entry_id"] for request in requests}
                and observation["data"].get("fulfilled_request_status") == "fulfilled"
                for observation in observations
            )
            assert any(
                entry["knowledge_type"] == "hypothesis" and entry["revision"] > 1
                for entry in history
            )
            assert accepted_current
        elif case_id == "dns_fault":
            assert accepted_current
            assert any("resolution" in hypothesis["data"].get("claim", "").lower() for hypothesis in accepted_current)
            assert any(
                record.get("name") == "dns_nxdomain_rate_pct"
                for entry in entries
                if entry["knowledge_type"] == "observation"
                for record in entry["data"].get("records", [])
            )
        else:
            assert not accepted_current
            assert not solutions
            requests = [entry for entry in entries if entry["knowledge_type"] == "evidence_request"]
            query_signatures = {
                json.dumps(
                    [entry["data"].get("requested_tool"), entry["data"].get("query")],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for entry in requests
            }
            assert len(query_signatures) == len(requests)
            assert problem_status["unresolved_requests"]
            assert all(
                request.get("evidence_status") == "unavailable"
                for request in problem_status["unresolved_requests"]
            )

        if expected_status == "completed":
            assert solutions
            solution = solutions[-1]
            observation_ids = {
                entry["entry_id"] for entry in entries
                if entry["knowledge_type"] == "observation"
            }
            assert solution["data"].get("evidence_ids")
            assert set(solution["data"]["evidence_ids"]).issubset(observation_ids)
            assert solution["data"].get("runbook_ids")
            assert any(
                review["data"].get("target_entry_id") == solution["entry_id"]
                and review["data"].get("target_revision") == solution["revision"]
                and review["data"].get("verdict") == "accept"
                for review in reviews
            )

        if case_id == "db_pool":
            assert problem_status["unresolved_requests"] == []

        assert "ground_truth" not in json.dumps(entries).lower()
        observed[case_id] = {
            "status": run["status"],
            "dispatch_count": len(dispatch_started),
            "task_ids": [finished_by_id[event["data"]["dispatch_id"]]["task_id"] for event in dispatch_started],
            "revisions": len(history),
        }

    # The compact agent context excludes fixture records; access to answer keys
    # is confined to the evaluation script after a completed run.
    summary = read_case_summary("db_pool")
    assert set(summary) == {"case_id", "title", "symptoms", "observed_at"}
    assert "metrics" not in summary and "logs" not in summary
    assert set(observed) == set(expectations)


class _OpenAICompatibleHandler:
    """Factory for an HTTP handler that records request shape without logging secrets."""

    @staticmethod
    def build(responses: list[tuple[Any, ...]], seen: list[dict[str, Any]]):
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                seen.append(
                    {
                        "path": self.path,
                        "body": json.loads(raw),
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                response = responses.pop(0)
                status, payload = response[0], response[1]
                if len(response) > 2:
                    time.sleep(float(response[2]))
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        return Handler


def test_live_adapter_uses_fake_http_endpoint_caps_output_and_surfaces_errors() -> None:
    from http.server import ThreadingHTTPServer
    from threading import Thread

    seen: list[dict[str, Any]] = []
    responses: list[tuple[Any, ...]] = [
        (
            200,
            {
                "choices": [{"message": {"content": '{"tool":"query_metrics"}'}}],
                "usage": {"prompt_tokens": 23, "completion_tokens": 11, "total_tokens": 34, "discard_me": 1},
            },
        ),
        (200, {"choices": []}),
        (503, {"error": {"message": "fake local provider unavailable"}}),
        (
            200,
            {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"input_tokens": 17, "output_tokens": 4},
            },
        ),
        (
            200,
            {"choices": [{"message": {"content": '{"too_slow":true}'}}]},
            0.3,
        ),
    ]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler.build(responses, seen))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    secret = f"local-test-{uuid.uuid4().hex}"
    adapter = OpenAICompatibleAdapter(
        f"http://127.0.0.1:{server.server_port}/v1",
        "fake-local-model",
        api_key=secret,
        timeout=2,
        max_output_tokens=73,
        token_limit_parameter="max_completion_tokens",
    )
    try:
        content = asyncio.run(
            adapter.complete(
                role="evidence",
                purpose="collect_evidence",
                instructions="Return one tool decision.",
                context={"case_id": "db_pool"},
                output_schema={"type": "object"},
            )
        )
        assert content == '{"tool":"query_metrics"}'
        assert seen[0]["path"] == "/v1/chat/completions"
        assert seen[0]["body"]["model"] == "fake-local-model"
        assert seen[0]["body"]["max_completion_tokens"] == 73
        assert seen[0]["authorization"] == f"Bearer {secret}"
        assert adapter.last_call_metadata["usage"] == {
            "prompt_tokens": 23,
            "completion_tokens": 11,
            "total_tokens": 34,
        }
        assert adapter.last_call_metadata["duration_ms"] >= 0
        assert secret not in json.dumps(adapter.last_call_metadata)

        with pytest.raises(ModelAdapterError, match="no chat completion content"):
            asyncio.run(
                adapter.complete(
                    role="diagnosis",
                    purpose="diagnose",
                    instructions="Return JSON.",
                    context={},
                    output_schema={"type": "object"},
                )
            )
        assert adapter.last_call_metadata["usage"] is None

        with pytest.raises(ModelAdapterError, match="HTTP 503"):
            asyncio.run(
                adapter.complete(
                    role="diagnosis",
                    purpose="diagnose",
                    instructions="Return JSON.",
                    context={},
                    output_schema={"type": "object"},
                )
            )
        assert adapter.last_call_metadata["usage"] is None

        configured = model_from_settings(
            Settings(
                model_mode="live",
                model_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model_name="settings-backed-model",
                model_api_key=secret,
                model_token_limit_parameter="max_tokens",
                model_timeout_seconds=0.08,
                model_max_output_tokens=41,
            )
        )
        assert isinstance(configured, OpenAICompatibleAdapter)
        assert configured.timeout == 0.08
        assert configured.max_output_tokens == 41
        assert asyncio.run(
            configured.complete(
                role="review",
                purpose="review_hypotheses",
                instructions="Return a JSON review.",
                context={},
                output_schema={"type": "object"},
            )
        ) == '{"ok":true}'
        assert seen[3]["body"]["max_tokens"] == 41

        started = time.monotonic()
        with pytest.raises(ModelAdapterError, match="request failed"):
            asyncio.run(
                configured.complete(
                    role="review",
                    purpose="review_hypotheses",
                    instructions="Return a JSON review.",
                    context={},
                    output_schema={"type": "object"},
                )
            )
        assert time.monotonic() - started < 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
