from __future__ import annotations

import asyncio
import time

import pytest
from datetime import datetime, timezone

from campus_blackboard.controller.runner import ControllerRunner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryBlackboard:
    """Small application-contract fake; no network, provider, or DB is used."""

    def __init__(self) -> None:
        self.problem = {
            "problem_id": "problem-demo",
            "case_id": "db_pool",
            "title": "Student portal intermittent latency",
            "status": "open",
            "board_revision": 0,
            "limits": {"max_dispatches": 12, "timeout_seconds": 30},
        }
        self.entries: list[dict] = []
        self.history: list[dict] = []
        self.request_ids: set[str] = set()

    def add(self, entry_id: str, kind: str, data: dict, *, revision: int = 1, actor: str = "fixture-agent") -> dict:
        self.problem["board_revision"] += 1
        if kind == "hypothesis":
            data = {"observation_refs": [
                {"entry_id": item["entry_id"], "revision": item["revision"]}
                for item in self.entries if item["knowledge_type"] == "observation"
            ], **data}
        record = {
            "entry_id": entry_id,
            "problem_id": self.problem["problem_id"],
            "knowledge_type": kind,
            "source_agent": actor,
            "revision": revision,
            "data": data,
            "created_at": _now(),
        }
        self.history.append(record)
        self.entries = [e for e in self.entries if e["entry_id"] != entry_id]
        self.entries.append(record)
        return record

    async def call(self, action: str, **params: dict) -> dict:
        if action == "read_blackboard":
            return {
                "problem": dict(self.problem),
                "entries": [dict(e) for e in self.entries],
                "history": [dict(e) for e in self.history],
            }
        if action == "write_blackboard":
            request_id = params["request_id"]
            if request_id in self.request_ids:
                return {"ok": True, "duplicate": True}
            self.request_ids.add(request_id)
            entry_id = f"controller-{len(self.entries)}-{self.problem['board_revision']}"
            self.add(entry_id, params["knowledge_type"], params["data"], actor="controller")
            return {"ok": True, "entry_id": entry_id}
        if action == "set_problem_status":
            self.problem["status"] = params["status"]
            self.problem["board_revision"] += 1
            return {"ok": True, "status": params["status"]}
        raise AssertionError(f"Unexpected Blackboard action: {action}")


@pytest.mark.parametrize("dispatch_limit,token_limit", [(12, 12000), (9, 12000), (12, 9), (9, 9)])
def test_controller_runs_conditional_evidence_loop_to_reviewed_solution(dispatch_limit, token_limit) -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()
        calls: list[tuple[str, str]] = []

        async def specialist(role: str, payload: dict) -> dict:
            assert set(payload) == {"problem_id", "purpose", "dispatch_id"}
            calls.append((role, payload["purpose"]))
            entries = board.entries
            if role == "evidence":
                requested = next(
                    (e for e in entries if e["knowledge_type"] == "evidence_request"), None
                )
                if requested:
                    board.add(
                        "obs-followup",
                        "observation",
                        {
                            "tool_name": "query_metrics",
                            "records": [{"name": "db_pool_active_connections", "value": 40}],
                            "fulfilled_request_entry_id": requested["entry_id"],
                            "fulfilled_request_status": "fulfilled",
                        },
                    )
                    return {"ok": True, "entry_ids": ["obs-followup"], "summary": "Returned pool metrics"}
                board.add(
                    "obs-initial",
                    "observation",
                    {"tool_name": "query_metrics", "records": [{"name": "request_latency_p95_ms", "value": 1840}]},
                )
                return {"ok": True, "entry_ids": ["obs-initial"], "summary": "Returned initial metrics"}

            if role == "diagnosis":
                observation_ids = [e["entry_id"] for e in entries if e["knowledge_type"] == "observation"]
                h1 = next((e for e in entries if e["entry_id"] == "hyp-db-pool"), None)
                h2 = next((e for e in entries if e["entry_id"] == "hyp-traffic"), None)
                revision = (h1["revision"] + 1) if h1 else 1
                board.add(
                    "hyp-db-pool",
                    "hypothesis",
                    {
                        "claim": "Database connection pool exhaustion",
                        "status": "supported",
                        "supporting_evidence_ids": observation_ids,
                        "contradicting_evidence_ids": [],
                        "open_questions": [],
                    },
                    revision=revision,
                )
                board.add(
                    "hyp-traffic",
                    "hypothesis",
                    {
                        "claim": "Traffic overload",
                        "status": "weakened",
                        "supporting_evidence_ids": [observation_ids[0]],
                        "contradicting_evidence_ids": observation_ids[1:],
                        "open_questions": [],
                    },
                    revision=(h2["revision"] + 1) if h2 else 1,
                )
                return {"ok": True, "entry_ids": ["hyp-db-pool", "hyp-traffic"], "summary": "Compared two explanations"}

            if role == "review" and payload["purpose"] == "review_hypotheses":
                hypothesis = next(
                    e for e in entries if e["knowledge_type"] == "hypothesis" and not any(
                        r["knowledge_type"] == "review"
                        and r["data"].get("target_entry_id") == e["entry_id"]
                        and r["data"].get("target_revision") == e["revision"]
                        for r in entries
                    )
                )
                if hypothesis["revision"] == 1 and hypothesis["entry_id"] == "hyp-db-pool":
                    board.add(
                        "review-request",
                        "review",
                        {
                            "target_entry_id": hypothesis["entry_id"],
                            "target_revision": hypothesis["revision"],
                            "verdict": "request_evidence",
                            "rationale": "Pool saturation needs a direct measurement.",
                            "supporting_evidence_ids": [],
                            "required_follow_up": {
                                "question": "What is the active connection count and pool limit?",
                                "requested_tool": "query_metrics",
                                "query": {"metric": "db_pool_active_connections"},
                                "reason": "Distinguish pool exhaustion from traffic load.",
                                "status": "open",
                            },
                        },
                    )
                    board.add(
                        "request-pool-count",
                        "evidence_request",
                        {
                            "question": "What is the active connection count and pool limit?",
                            "requested_tool": "query_metrics",
                            "query": {"metric": "db_pool_active_connections"},
                            "reason": "Distinguish pool exhaustion from traffic load.",
                            "status": "open",
                        },
                    )
                    return {"ok": True, "entry_ids": ["review-request", "request-pool-count"], "summary": "Requested one measurement"}
                board.add(
                    f"review-{hypothesis['entry_id']}-{hypothesis['revision']}",
                    "review",
                    {
                        "target_entry_id": hypothesis["entry_id"],
                        "target_revision": hypothesis["revision"],
                        "verdict": "accept",
                        "rationale": "The claim follows the cited fixture observations.",
                        "supporting_evidence_ids": hypothesis["data"].get("supporting_evidence_ids", []),
                        "required_follow_up": None,
                    },
                )
                return {"ok": True, "entry_ids": ["review"], "summary": "Reviewed exact hypothesis revision"}

            if role == "response":
                board.add(
                    "solution-db-pool",
                    "solution",
                    {
                        "summary": "Inspect pool wait time and connection lifecycle before making a bounded capacity change.",
                        "actions": [{"step": "Check long-lived connections", "purpose": "Find leaked or stalled clients", "expected_check": "Pool wait count falls"}],
                        "caveats": ["No remediation has been executed."],
                        "evidence_ids": [e["entry_id"] for e in entries if e["knowledge_type"] == "observation"],
                        "runbook_ids": ["database-connection-pool"],
                    },
                )
                return {"ok": True, "entry_ids": ["solution-db-pool"], "summary": "Prepared a runbook-backed response"}

            if role == "review" and payload["purpose"] == "review_solution":
                solution = next(e for e in entries if e["knowledge_type"] == "solution")
                board.add(
                    "review-solution",
                    "review",
                    {
                        "target_entry_id": solution["entry_id"],
                        "target_revision": solution["revision"],
                        "verdict": "accept",
                        "rationale": "Proposal includes evidence and a runbook reference.",
                        "supporting_evidence_ids": solution["data"]["evidence_ids"],
                        "required_follow_up": None,
                    },
                )
                return {"ok": True, "entry_ids": ["review-solution"], "summary": "Accepted exact solution revision"}

            raise AssertionError(f"Unexpected specialist task: {role} {payload['purpose']}")

        board.problem["limits"]["max_dispatches"] = dispatch_limit
        board.problem["limits"]["max_run_tokens"] = token_limit
        async def measured_specialist(role, payload):
            result = await specialist(role, payload)
            result["model_usage"] = [{"usage": {"total_tokens": 1}}]
            return result
        result = await ControllerRunner(board, measured_specialist, max_dispatches=12, timeout_seconds=20).run("problem-demo")
        assert result.status == "completed"
        assert result.reason == "current_supported_diagnosis_and_solution_accepted"
        assert result.dispatch_count <= 12
        assert calls[0] == ("evidence", "collect_evidence")
        assert ("evidence", "collect_evidence") in calls
        assert calls.count(("diagnosis", "diagnose")) == 2
        assert calls[-1] == ("review", "review_solution")
        assert len({event["data"]["dispatch_id"] for event in board.entries if event["knowledge_type"] == "controller_decision" and event["data"].get("event_type") == "dispatch_started"}) == result.dispatch_count
        assert any(
            e["knowledge_type"] == "controller_decision"
            and e["data"].get("trigger", {}).get("rule") == "open_evidence_request"
            for e in board.entries
        )
        assert board.problem["status"] == "completed"

    asyncio.run(scenario())


def test_reported_token_ceiling_stops_before_another_dispatch() -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()
        calls = 0

        async def specialist(role: str, payload: dict) -> dict:
            nonlocal calls
            calls += 1
            board.add("obs-1", "observation", {"tool_name": "query_metrics", "records": []})
            return {
                "ok": True,
                "entry_ids": ["obs-1"],
                "model_usage": [{"mode": "live", "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}}],
            }

        result = await ControllerRunner(
            board, specialist, max_dispatches=12, timeout_seconds=20, max_run_tokens=10
        ).run("problem-demo")
        assert result.status == "unresolved"
        assert result.reason == "model_token_limit_reached"
        assert result.observed_model_tokens == 11
        assert result.model_usage_state == "complete"
        assert result.max_run_tokens == 10
        assert calls == 1

    asyncio.run(scenario())


def test_missing_usage_is_reported_unmeasured_not_estimated() -> None:
    async def scenario() -> None:
        board = MemoryBlackboard()

        async def specialist(role: str, payload: dict) -> dict:
            board.add("obs-1", "observation", {"tool_name": "query_metrics", "records": []})
            return {"ok": True, "entry_ids": ["obs-1"], "model_usage": [{"mode": "offline_fixture", "usage": None}]}

        result = await ControllerRunner(
            board, specialist, max_dispatches=1, timeout_seconds=20, max_run_tokens=10
        ).run("problem-demo")
        assert result.status == "unresolved"
        assert result.observed_model_tokens == 0
        assert result.model_usage_state == "unmeasured"

    asyncio.run(scenario())


@pytest.mark.parametrize("slow_action,slow_read_number", [("read_blackboard", 1), ("read_blackboard", 2), ("write_blackboard", 0)])
def test_deadline_during_board_operation_never_starts_specialist(slow_action, slow_read_number) -> None:
    async def scenario():
        class SlowBoard(MemoryBlackboard):
            reads = 0
            async def call(self, action, **params):
                if action == "read_blackboard":
                    self.reads += 1
                if action == slow_action and (action != "read_blackboard" or self.reads == slow_read_number):
                    await asyncio.sleep(1.2)
                return await super().call(action, **params)
        board = SlowBoard()
        board.problem["limits"]["timeout_seconds"] = 1
        calls = []
        async def specialist(role, payload):
            calls.append(role)
            return {"ok": True}
        started = time.monotonic()
        result = await ControllerRunner(board, specialist, timeout_seconds=1).run("problem-demo")
        assert result.reason == "case_deadline_reached"
        assert calls == []
        assert time.monotonic() - started < 3.3
    asyncio.run(scenario())


def test_real_missing_evidence_fixture_reaches_unavailable_result(tmp_path) -> None:
    from campus_blackboard.agents.runner import SpecialistRunner
    from campus_blackboard.blackboard.repository import Repository
    from campus_blackboard.model_adapter import OfflineFixtureAdapter

    async def scenario():
        repository = Repository(tmp_path / "missing.sqlite3")
        class Board:
            def __init__(self, actor):
                self.actor = actor
            async def call(self, action, **params):
                result = repository.handle(self.actor, {"action": action, **params})
                assert "error" not in result, result
                return result
        try:
            created = repository.handle("controller", {
                "action": "create_problem", "request_id": "missing-case",
                "case_id": "missing_evidence", "title": "Missing evidence regression",
            })
            problem_id = created["problem_id"]
            async def specialist(role, payload):
                return await SpecialistRunner(role, blackboard=Board(role), model=OfflineFixtureAdapter()).run(payload)
            result = await ControllerRunner(Board("controller"), specialist).run(problem_id)
            board = await Board("dashboard").call("read_blackboard", problem_id=problem_id)
            assert result.status == "unresolved"
            assert result.reason != "diagnosis_has_stale_evidence"
            assert any(e["data"].get("fulfilled_request_status") == "unavailable" for e in board["entries"])
            assert any(e["knowledge_type"] == "review" for e in board["entries"])
            assert not any(e["knowledge_type"] == "solution" for e in board["entries"])
        finally:
            repository.close()
    asyncio.run(scenario())
