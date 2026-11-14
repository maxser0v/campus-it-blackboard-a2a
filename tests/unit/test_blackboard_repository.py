from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from campus_blackboard.blackboard.repository import Repository


def _create_problem(repo: Repository, problem_id: str = "problem-a") -> dict:
    return repo.handle(
        "controller",
        {
            "action": "create_problem",
            "request_id": f"create-{problem_id}",
            "problem_id": problem_id,
            "case_id": "db_pool",
            "title": "Portal latency",
            "limits": {"max_dispatches": 12},
        },
    )


def _write(repo: Repository, problem_id: str, actor: str, kind: str, data: dict, request_id: str) -> dict:
    return repo.handle(
        actor,
        {
            "action": "write_blackboard",
            "request_id": request_id,
            "problem_id": problem_id,
            "knowledge_type": kind,
            "data": data,
        },
    )


def test_persistence_idempotency_and_canonical_author(tmp_path):
    database = tmp_path / "blackboard.sqlite3"
    repo = Repository(database)
    assert _create_problem(repo)["problem_id"] == "problem-a"

    request = {
        "action": "write_blackboard",
        "request_id": "write-observation-1",
        "problem_id": "problem-a",
        "knowledge_type": "observation",
        "data": {
            "source_agent": "forged-from-model",
            "tool_name": "query_metrics",
            "records": [{"name": "pool occupancy", "value": 100, "unit": "%"}],
        },
    }
    first = repo.handle("evidence", request)
    repeated = repo.handle("evidence", request)
    assert first["entry_id"] == repeated["entry_id"]
    assert repeated["duplicate"] is True
    assert first["source_agent"] == "evidence"
    repo.close()

    reopened = Repository(database)
    snapshot = reopened.handle("dashboard", {"action": "read_blackboard", "problem_id": "problem-a"})
    assert snapshot["problem"]["board_revision"] == 1
    assert snapshot["problem"]["limits"] == {"max_dispatches": 12}
    assert len(snapshot["entries"]) == 1
    assert snapshot["entries"][0]["source_agent"] == "evidence"
    assert snapshot["history"][0]["entry_id"] == first["entry_id"]
    reopened.close()


def test_append_only_revision_ownership_and_stale_conflict(tmp_path):
    repo = Repository(tmp_path / "board.sqlite3")
    _create_problem(repo)
    created = _write(repo, "problem-a", "diagnosis", "hypothesis", {"claim": "Pool exhaustion"}, "hyp-1")
    entry_id = created["entry_id"]

    forbidden = repo.handle(
        "review",
        {
            "action": "update_knowledge",
            "request_id": "review-update",
            "problem_id": "problem-a",
            "entry_id": entry_id,
            "expected_revision": 1,
            "data": {"claim": "Changed by another role"},
        },
    )
    assert forbidden["error"]["code"] == "forbidden"

    update = repo.handle(
        "diagnosis",
        {
            "action": "update_knowledge",
            "request_id": "hyp-2",
            "problem_id": "problem-a",
            "entry_id": entry_id,
            "expected_revision": 1,
            "data": {"claim": "Pool exhaustion", "status": "supported"},
        },
    )
    assert update["revision"] == 2

    stale = repo.handle(
        "diagnosis",
        {
            "action": "update_knowledge",
            "request_id": "hyp-stale",
            "problem_id": "problem-a",
            "entry_id": entry_id,
            "expected_revision": 1,
            "data": {"claim": "Stale revision"},
        },
    )
    assert stale["error"]["code"] == "revision_conflict"

    snapshot = repo.handle("diagnosis", {"action": "read_blackboard", "problem_id": "problem-a"})
    assert [entry["revision"] for entry in snapshot["history"]] == [1, 2]
    assert snapshot["entries"][0]["revision"] == 2
    repo.close()


def test_references_are_valid_and_cannot_cross_problems(tmp_path):
    repo = Repository(tmp_path / "board.sqlite3")
    _create_problem(repo, "problem-a")
    _create_problem(repo, "problem-b")
    observation = _write(repo, "problem-a", "evidence", "observation", {"records": [1]}, "obs-a")

    missing = _write(
        repo,
        "problem-a",
        "diagnosis",
        "hypothesis",
        {"supporting_evidence_ids": ["missing-entry"]},
        "hyp-missing",
    )
    assert missing["error"]["code"] == "invalid_reference"

    cross_case = _write(
        repo,
        "problem-b",
        "diagnosis",
        "hypothesis",
        {"supporting_evidence_ids": [observation["entry_id"]]},
        "hyp-cross-case",
    )
    assert cross_case["error"]["code"] == "cross_case_reference"
    repo.close()


def test_status_controller_permission_and_dashboard_read_only(tmp_path):
    repo = Repository(tmp_path / "board.sqlite3")
    _create_problem(repo)
    denied_status = repo.handle(
        "diagnosis",
        {
            "action": "set_problem_status",
            "request_id": "status-forged",
            "problem_id": "problem-a",
            "status": "completed",
        },
    )
    assert denied_status["error"]["code"] == "forbidden"

    denied_write = _write(repo, "problem-a", "dashboard", "observation", {"claim": "no"}, "dashboard-write")
    assert denied_write["error"]["code"] == "forbidden"

    decision = _write(repo, "problem-a", "controller", "controller_decision", {"event_type": "dispatch_started"}, "decision")
    assert decision["source_agent"] == "controller"
    repo.close()


def test_only_one_concurrent_update_wins_expected_revision(tmp_path):
    repo = Repository(tmp_path / "board.sqlite3")
    _create_problem(repo)
    created = _write(repo, "problem-a", "diagnosis", "hypothesis", {"claim": "Pool exhaustion"}, "hyp-concurrent")
    barrier = Barrier(2)

    def update(index: int) -> dict:
        barrier.wait(timeout=5)
        return repo.handle(
            "diagnosis",
            {
                "action": "update_knowledge",
                "request_id": f"race-{index}",
                "problem_id": "problem-a",
                "entry_id": created["entry_id"],
                "expected_revision": 1,
                "data": {"claim": f"revision-{index}"},
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, (1, 2)))

    assert sum("error" not in result for result in results) == 1
    assert [result.get("error", {}).get("code") for result in results].count("revision_conflict") == 1
    snapshot = repo.handle("diagnosis", {"action": "read_blackboard", "problem_id": "problem-a"})
    assert len(snapshot["history"]) == 2
    repo.close()

