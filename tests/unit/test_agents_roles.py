from __future__ import annotations

import asyncio
import json

import pytest

from campus_blackboard.config import Settings
from campus_blackboard.agents.runner import (
    SpecialistRunError,
    SpecialistRunner,
    build_specialist_handler,
    _validate_diagnosis,
    _validate_review,
)
from campus_blackboard.model_adapter import OfflineFixtureAdapter


class FakeBlackboard:
    def __init__(self, actor: str, case_id: str, entries: list[dict] | None = None):
        self.actor = actor
        self.entries = list(entries or [])
        self.problem = {"problem_id": "problem-1", "case_id": case_id, "title": case_id, "status": "open", "board_revision": 1}
        self.calls = []
        self.counter = len(self.entries)

    async def call(self, action: str, **params):
        self.calls.append((action, params))
        if action == "read_blackboard":
            return {"problem": dict(self.problem), "entries": [dict(item) for item in self.entries], "history": [dict(item) for item in self.entries]}
        if action == "write_blackboard":
            self.counter += 1
            entry_id = f"entry-{self.counter}"
            entry = {"entry_id": entry_id, "problem_id": params["problem_id"], "knowledge_type": params["knowledge_type"], "source_agent": self.actor, "revision": 1, "data": params["data"], "created_at": "2026-11-06T00:00:00Z"}
            self.entries.append(entry)
            return {"entry_id": entry_id}
        if action == "update_knowledge":
            entry = next(item for item in self.entries if item["entry_id"] == params["entry_id"])
            entry["revision"] += 1
            entry["data"] = params["data"]
            return {"entry_id": entry["entry_id"], "revision": entry["revision"]}
        raise AssertionError(f"unexpected action: {action}")


class ScriptModel:
    mode = "test_script"

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.call_count = 0
        self.last_call_metadata = None

    async def complete(self, **kwargs):
        self.call_count += 1
        return json.dumps(self.responses.pop(0))


def _entry(entry_id, kind, data, *, source="diagnosis", revision=1):
    return {"entry_id": entry_id, "problem_id": "problem-1", "knowledge_type": kind, "source_agent": source, "revision": revision, "data": data, "created_at": "2026-11-06T00:00:00Z"}


def test_evidence_agent_fulfills_exact_targeted_request_and_preserves_result() -> None:
    query = {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}
    board = FakeBlackboard("evidence", "db_pool", [
        _entry("req-1", "evidence_request", {"question": "Check pool occupancy", "requested_tool": "query_metrics", "query": query, "reason": "Need the discriminating pool check", "status": "open"}),
    ])
    model = ScriptModel([{"tool": "query_metrics", "query": query, "rationale": "Checking the requested pool signals", "fulfilled_request_entry_id": "req-1"}])

    result = asyncio.run(SpecialistRunner("evidence", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "d-1"}))

    observation = board.entries[-1]
    assert result["ok"] is True
    assert observation["knowledge_type"] == "observation"
    assert observation["data"]["fulfilled_request_entry_id"] == "req-1"
    assert observation["data"]["fulfilled_request_status"] == "fulfilled"
    assert [row["value"] for row in observation["data"]["records"]] == [40, 40, 27]
    assert observation["data"]["source_ids"] == [row["source_id"] for row in observation["data"]["records"]]
    assert result["model_mode"] == "test_script"


def test_evidence_agent_marks_empty_targeted_result_unavailable() -> None:
    query = {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}
    board = FakeBlackboard("evidence", "missing_evidence", [
        _entry("req-2", "evidence_request", {"question": "Check pool", "requested_tool": "query_metrics", "query": query, "reason": "Need pool data", "status": "open"}),
    ])
    model = ScriptModel([{"tool": "query_metrics", "query": query, "rationale": "Checking requested metric names", "fulfilled_request_entry_id": "req-2"}])

    asyncio.run(SpecialistRunner("evidence", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "d-2"}))
    assert board.entries[-1]["data"]["records"] == []
    assert board.entries[-1]["data"]["fulfilled_request_status"] == "unavailable"


def test_diagnosis_does_not_support_claim_using_unavailable_observation() -> None:
    baseline = _entry("obs-1", "observation", {"tool_name": "query_metrics", "records": [{"name": "request_latency_p95_ms", "value": 720}, {"name": "request_error_rate_pct", "value": 6.1}], "source_ids": ["source-1"]}, source="evidence")
    unavailable = _entry("obs-2", "observation", {"tool_name": "query_metrics", "records": [], "source_ids": [], "fulfilled_request_entry_id": "req-1", "fulfilled_request_status": "unavailable"}, source="evidence")
    board = FakeBlackboard("diagnosis", "missing_evidence", [baseline, unavailable])

    asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=OfflineFixtureAdapter()).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "d-3"}))

    diagnosis = next(item for item in board.entries if item["knowledge_type"] == "hypothesis")
    assert diagnosis["data"]["status"] == "unresolved"
    assert diagnosis["data"]["supporting_evidence_ids"] == ["obs-1"]
    assert "obs-2" not in diagnosis["data"]["supporting_evidence_ids"]


def test_diagnosis_suppresses_repeated_unavailable_query_without_revising_unchanged_finding() -> None:
    query = {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}
    baseline = _entry("obs-base", "observation", {"tool_name": "query_metrics", "records": [{"name": "request_latency_p95_ms", "value": 720}], "source_ids": ["source-1"]}, source="evidence")
    request = _entry("req-prior", "evidence_request", {"question": "Check pool occupancy", "requested_tool": "query_metrics", "query": query, "reason": "Need pool measurements", "status": "open"}, source="diagnosis")
    unavailable = _entry("obs-unavailable", "observation", {"tool_name": "query_metrics", "query": query, "records": [], "source_ids": [], "fulfilled_request_entry_id": "req-prior", "fulfilled_request_status": "unavailable"}, source="evidence")
    finding_data = {"claim": "The available evidence is insufficient to identify the upstream cause", "status": "unresolved", "supporting_evidence_ids": ["obs-base"], "contradicting_evidence_ids": [], "open_questions": ["The requested targeted measurements were unavailable."]}
    finding_data["observation_refs"] = [{"entry_id": "obs-base", "revision": 1}, {"entry_id": "obs-unavailable", "revision": 1}]
    finding = _entry("hyp-unresolved", "hypothesis", finding_data)
    board = FakeBlackboard("diagnosis", "missing_evidence", [baseline, request, unavailable, finding])
    model = ScriptModel([{
        "hypotheses": [{**finding_data, "revise_entry_id": "hyp-unresolved"}],
        "evidence_requests": [{"question": "Are request rate and CPU elevated?", "requested_tool": "query_metrics", "query": {"metric_names": list(reversed(query["metric_names"]))}, "reason": "The prior request did not return records."}],
    }])

    result = asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "d-repeat"}))

    assert result["ok"] is True
    assert [item for item in board.entries if item["knowledge_type"] == "evidence_request"] == [request]
    assert finding["revision"] == 1


def test_diagnosis_can_request_new_query_after_different_query_was_unavailable() -> None:
    old_query = {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}
    baseline = _entry("obs-base", "observation", {"tool_name": "query_metrics", "records": [{"name": "request_latency_p95_ms", "value": 720}], "source_ids": ["source-1"]}, source="evidence")
    request = _entry("req-prior", "evidence_request", {"question": "Check pool occupancy", "requested_tool": "query_metrics", "query": old_query, "reason": "Need pool measurements", "status": "open"}, source="diagnosis")
    unavailable = _entry("obs-unavailable", "observation", {"tool_name": "query_metrics", "query": old_query, "records": [], "source_ids": [], "fulfilled_request_entry_id": "req-prior", "fulfilled_request_status": "unavailable"}, source="evidence")
    board = FakeBlackboard("diagnosis", "missing_evidence", [baseline, request, unavailable])
    model = ScriptModel([{
        "hypotheses": [],
        "evidence_requests": [{"question": "Search for upstream timeout logs", "requested_tool": "query_logs", "query": {"contains": "upstream timeout"}, "reason": "A different source may explain the 502 responses."}],
    }])

    asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "d-new-query"}))

    requests = [item for item in board.entries if item["knowledge_type"] == "evidence_request"]
    assert len(requests) == 2
    assert requests[-1]["data"]["requested_tool"] == "query_logs"


def test_external_diagnosis_validation_rejects_supported_claim_with_irrelevant_records() -> None:
    obs = _entry("obs-latency", "observation", {"records": [{"name": "request_latency_p95_ms", "value": 800}]}, source="evidence")
    with pytest.raises(ValueError, match="claim-relevant"):
        _validate_diagnosis({"hypotheses": [{"claim": "Database connection pool exhaustion", "status": "supported", "supporting_evidence_ids": ["obs-latency"], "contradicting_evidence_ids": [], "open_questions": []}], "evidence_requests": []}, [obs], "diagnosis")


def test_offline_review_checks_raw_content_not_only_citation_ids() -> None:
    obs = _entry("obs-latency", "observation", {"records": [{"name": "request_latency_p95_ms", "value": 800}]}, source="evidence")
    target = _entry("hyp-1", "hypothesis", {"claim": "Database connection pool exhaustion", "status": "supported", "supporting_evidence_ids": ["obs-latency"], "open_questions": ["What are active pool connections?"]}, revision=1)
    target["data"]["observation_refs"] = [{"entry_id": obs["entry_id"], "revision": obs["revision"]}]
    board = FakeBlackboard("review", "db_pool", [obs, target])

    result = asyncio.run(SpecialistRunner("review", blackboard=board, model=OfflineFixtureAdapter()).run({"problem_id": "problem-1", "purpose": "review_hypotheses", "dispatch_id": "d-4"}))

    review = next(item for item in board.entries if item["knowledge_type"] == "review")
    request = next(item for item in board.entries if item["knowledge_type"] == "evidence_request")
    assert review["data"]["verdict"] == "request_evidence"
    assert request["data"]["requested_tool"] == "query_metrics"
    assert request["data"]["query"]["metric_names"] == ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]
    assert result["entry_ids"] == [review["entry_id"], request["entry_id"]]


def test_offline_review_does_not_reopen_an_unavailable_fixture_query() -> None:
    query = {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}
    observation = _entry("obs-base", "observation", {"tool_name": "query_metrics", "records": [{"name": "request_latency_p95_ms", "value": 720}]}, source="evidence")
    request = _entry("req-prior", "evidence_request", {"question": "Check pool occupancy", "requested_tool": "query_metrics", "query": query, "reason": "Need pool measurements", "status": "open"}, source="diagnosis")
    unavailable = _entry("obs-unavailable", "observation", {"tool_name": "query_metrics", "query": query, "records": [], "source_ids": [], "fulfilled_request_entry_id": "req-prior", "fulfilled_request_status": "unavailable"}, source="evidence")
    target = _entry("hyp-1", "hypothesis", {"claim": "The available evidence is insufficient to identify the upstream cause", "status": "unresolved", "supporting_evidence_ids": ["obs-base"], "contradicting_evidence_ids": [], "open_questions": ["The requested targeted measurements were unavailable."]})
    target["data"]["observation_refs"] = [
        {"entry_id": entry["entry_id"], "revision": entry["revision"]}
        for entry in (observation, unavailable)
    ]
    board = FakeBlackboard("review", "missing_evidence", [observation, request, unavailable, target])

    asyncio.run(SpecialistRunner("review", blackboard=board, model=OfflineFixtureAdapter()).run({"problem_id": "problem-1", "purpose": "review_hypotheses", "dispatch_id": "d-review-unavailable"}))

    reviews = [item for item in board.entries if item["knowledge_type"] == "review"]
    assert len(reviews) == 1
    assert reviews[0]["data"]["verdict"] == "reject"
    assert reviews[0]["data"]["required_follow_up"] is None
    assert len([item for item in board.entries if item["knowledge_type"] == "evidence_request"]) == 1


def test_diagnosis_revises_its_own_hypothesis_after_targeted_fixture_evidence() -> None:
    baseline = _entry("obs-base", "observation", {"records": [{"name": "request_latency_p95_ms", "value": 1840}, {"name": "request_error_rate_pct", "value": 8.2}, {"name": "app_cpu_pct", "value": 34}]}, source="evidence")
    pool = _entry("obs-pool", "observation", {"records": [{"name": "db_pool_active_connections", "value": 40}, {"name": "db_pool_max_connections", "value": 40}, {"name": "db_pool_wait_count", "value": 27}]}, source="evidence")
    first = _entry("hyp-connection", "hypothesis", {"claim": "A constrained downstream connection resource may be contributing to slow requests", "status": "proposed", "supporting_evidence_ids": ["obs-base"], "contradicting_evidence_ids": [], "open_questions": ["What are pool measurements?"]})
    second = _entry("hyp-demand", "hypothesis", {"claim": "Elevated incoming demand may be contributing to observed request failures", "status": "proposed", "supporting_evidence_ids": ["obs-base"], "contradicting_evidence_ids": [], "open_questions": ["Is demand elevated?"]})
    board = FakeBlackboard("diagnosis", "db_pool", [baseline, pool, first, second])

    asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=OfflineFixtureAdapter()).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "d-revise"}))

    revised = [item for item in board.entries if item["knowledge_type"] == "hypothesis"]
    assert len(revised) == 2
    supported = next(item for item in revised if item["data"]["status"] == "supported")
    weakened = next(item for item in revised if item["data"]["status"] == "weakened")
    assert supported["entry_id"] == "hyp-connection"
    assert supported["revision"] == 2
    assert "obs-pool" in supported["data"]["supporting_evidence_ids"]
    assert weakened["entry_id"] == "hyp-demand"
    assert weakened["revision"] == 2
    assert "obs-pool" in weakened["data"]["contradicting_evidence_ids"]


def test_response_uses_retrieved_runbook_sections_and_current_accepted_hypothesis() -> None:
    observation = _entry("obs-pool", "observation", {"records": [{"name": "db_pool_active_connections", "value": 40}, {"name": "db_pool_max_connections", "value": 40}, {"name": "db_pool_wait_count", "value": 27}]}, source="evidence")
    hypothesis = _entry("hyp-1", "hypothesis", {"claim": "Database connection acquisition is constrained by high pool occupancy", "status": "supported", "supporting_evidence_ids": ["obs-pool"], "contradicting_evidence_ids": [], "open_questions": []}, source="diagnosis")
    review = _entry("review-1", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept", "supporting_evidence_ids": ["obs-pool"]}, source="review")
    board = FakeBlackboard("response", "db_pool", [observation, hypothesis, review])

    result = asyncio.run(SpecialistRunner("response", blackboard=board, model=OfflineFixtureAdapter()).run({"problem_id": "problem-1", "purpose": "propose_response", "dispatch_id": "d-response"}))

    solution = next(item for item in board.entries if item["knowledge_type"] == "solution")
    assert solution["data"]["evidence_ids"] == ["obs-pool"]
    assert solution["data"]["hypothesis_refs"] == [{"entry_id": "hyp-1", "revision": 1}]
    assert solution["data"]["runbook_ids"]
    assert all(runbook_id.startswith("RB-DB-POOL-01#") for runbook_id in solution["data"]["runbook_ids"])
    assert solution["data"]["actions"]
    assert result["entry_ids"] == [solution["entry_id"]]


def test_live_review_validation_rejects_unrelated_observation_as_support() -> None:
    obs = _entry("obs-latency", "observation", {"records": [{"name": "request_latency_p95_ms", "value": 800}]}, source="evidence")
    target = _entry("hyp-1", "hypothesis", {"claim": "Database connection pool exhaustion", "status": "supported", "supporting_evidence_ids": ["obs-latency"]}, revision=1)
    output = {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept", "rationale": "IDs exist", "supporting_evidence_ids": ["obs-latency"], "required_follow_up": None}
    with pytest.raises(ValueError, match="relevant"):
        _validate_review(output, target, [obs, target], "review_hypotheses", set())


def test_invalid_model_output_gets_one_repair_then_explicit_error() -> None:
    board = FakeBlackboard("evidence", "db_pool", [])
    model = ScriptModel([{"tool": "shell", "query": {}, "rationale": "no"}, {"tool": "shell", "query": {}, "rationale": "still no"}])
    runner = SpecialistRunner("evidence", blackboard=board, model=model)

    with pytest.raises(SpecialistRunError, match="after one repair attempt"):
        asyncio.run(runner.run({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "d-5"}))
    assert model.call_count == 2
    assert not any(action == "write_blackboard" for action, _ in board.calls)


def test_hosted_handler_isolates_usage_for_concurrent_tasks(monkeypatch) -> None:
    created_models = []

    class UsageModel:
        mode = "test_usage"

        def __init__(self, token_total: int):
            self.token_total = token_total
            self.last_call_metadata = None

        async def complete(self, **kwargs):
            del kwargs
            await asyncio.sleep(0.01)
            self.last_call_metadata = {
                "mode": self.mode,
                "model": f"test-{self.token_total}",
                "duration_ms": 10,
                "usage": {"total_tokens": self.token_total},
            }
            return json.dumps({
                "tool": "query_metrics",
                "query": {"metric_names": ["request_latency_p95_ms"]},
                "rationale": "Read one metric for this task.",
                "fulfilled_request_entry_id": None,
            })

    def make_model(settings):
        del settings
        model = UsageModel(101 if not created_models else 202)
        created_models.append(model)
        return model

    monkeypatch.setattr("campus_blackboard.agents.runner.model_from_settings", make_model)
    board = FakeBlackboard("evidence", "db_pool")
    handler = build_specialist_handler("evidence", Settings(), blackboard=board)

    async def invoke_both():
        return await asyncio.gather(
            handler({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "usage-a"}),
            handler({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "usage-b"}),
        )

    results = asyncio.run(invoke_both())

    assert len(created_models) == 2
    assert all(len(result["model_usage"]) == 1 for result in results)
    assert sorted(result["model_usage"][0]["usage"]["total_tokens"] for result in results) == [101, 202]


@pytest.mark.parametrize("query", [
    {"contains": 7}, {"service": 7}, {"contains": "error", "limit": 0},
    {"contains": "error", "limit": True}, {"contains": "error", "limit": 101},
    {"contains": "error", "level": "fatal"}, {"contains": "x" * 201},
])
def test_invalid_tool_arguments_are_repaired_before_execution(query) -> None:
    bad = {"tool": "query_logs", "query": query, "rationale": "Inspect logs", "fulfilled_request_entry_id": None}
    good = {**bad, "query": {"contains": "NXDOMAIN", "service": "dns", "level": "error", "limit": 10}}
    model = ScriptModel([bad, good])
    board = FakeBlackboard("evidence", "dns_fault")
    result = asyncio.run(SpecialistRunner("evidence", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "repair-args"}))
    assert result["ok"] and model.call_count == 2
    assert board.entries[-1]["data"]["query"] == good["query"]


def test_invalid_evidence_request_is_not_persisted() -> None:
    bad = {"hypotheses": [], "evidence_requests": [{"question": "Inspect logs?", "reason": "Need logs", "requested_tool": "query_logs", "query": {"contains": 7}}]}
    board = FakeBlackboard("diagnosis", "db_pool")
    model = ScriptModel([bad, bad])
    with pytest.raises(SpecialistRunError, match="after one repair"):
        asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "bad-request"}))
    assert not board.entries


def test_evidence_dispatch_replays_persisted_result_after_restart_and_lost_ack(tmp_path, monkeypatch) -> None:
    from campus_blackboard.blackboard.repository import Repository
    repo = Repository(tmp_path / "board.db")
    repo.handle("controller", {"action": "create_problem", "problem_id": "problem-1", "request_id": "create", "case_id": "db_pool", "title": "Case"})
    class Board:
        actor = "evidence"
        lose_ack = True
        async def call(self, action, **params):
            result = repo.handle(self.actor, {"action": action, **params})
            if action == "write_blackboard" and self.lose_ack:
                self.lose_ack = False
                raise RuntimeError("connection lost after commit")
            return result
    board = Board()
    payload = {"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "same-dispatch"}
    monkeypatch.setattr("campus_blackboard.tools.fixtures._now", lambda: "2026-11-07T00:00:01Z")
    first = asyncio.run(SpecialistRunner("evidence", blackboard=board, model=OfflineFixtureAdapter()).run(payload))
    monkeypatch.setattr("campus_blackboard.tools.fixtures._now", lambda: "2026-11-07T00:00:02Z")
    class NoTools:
        def execute(self, *args, **kwargs):
            raise AssertionError("replay must not execute tools")
    no_model = ScriptModel([])
    replay = asyncio.run(SpecialistRunner("evidence", blackboard=board, model=no_model, tools=NoTools()).run(payload))
    second = asyncio.run(SpecialistRunner("evidence", blackboard=board, model=no_model, tools=NoTools()).run(payload))
    assert first == replay == second
    assert no_model.call_count == 0
    assert replay["model_mode"] == "offline_fixture"
    snapshot = repo.handle("evidence", {"action": "read_blackboard", "problem_id": "problem-1"})
    assert len(snapshot["entries"]) == 1
    assert replay["entry_ids"] == [snapshot["entries"][0]["entry_id"]]
    # A different dispatch is not a replay of the first one.
    with pytest.raises(IndexError):
        asyncio.run(SpecialistRunner("evidence", blackboard=board, model=no_model).run({**payload, "dispatch_id": "another"}))
    repo.close()


def test_diagnosis_records_actual_observation_revisions_not_model_refs() -> None:
    obs = _entry("obs", "observation", {"records": [{"name": "request_latency_p95_ms", "value": 900}]}, source="evidence", revision=3)
    board = FakeBlackboard("diagnosis", "db_pool", [obs])
    model = ScriptModel([{"hypotheses": [{"claim": "Cause remains unknown", "status": "unresolved", "supporting_evidence_ids": ["obs"], "observation_refs": [{"entry_id": "fake", "revision": 99}]}], "evidence_requests": []}])
    asyncio.run(SpecialistRunner("diagnosis", blackboard=board, model=model).run({"problem_id": "problem-1", "purpose": "diagnose", "dispatch_id": "refs"}))
    assert board.entries[-1]["data"]["observation_refs"] == [{"entry_id": "obs", "revision": 3}]



def test_concurrent_evidence_deliveries_return_one_canonical_result(tmp_path) -> None:
    from campus_blackboard.blackboard.repository import Repository
    repo = Repository(tmp_path / "board.db")
    repo.handle("controller", {"action": "create_problem", "problem_id": "problem-1", "request_id": "create", "case_id": "db_pool", "title": "Case"})
    class Board:
        actor = "evidence"
        reads = 0
        async def call(self, action, **params):
            result = repo.handle(self.actor, {"action": action, **params})
            if action == "read_blackboard":
                self.reads += 1
                if self.reads <= 2:
                    await asyncio.sleep(0)
            return result
    board = Board()
    payload = {"problem_id": "problem-1", "purpose": "collect_evidence", "dispatch_id": "concurrent"}
    choice = {"tool": "query_logs", "query": {"contains": "error"}, "rationale": "First rationale", "fulfilled_request_entry_id": None}
    async def both():
        return await asyncio.gather(*[
            SpecialistRunner("evidence", blackboard=board, model=ScriptModel([{**choice, "rationale": rationale}])).run(payload)
            for rationale in ("First rationale", "Different regenerated rationale")
        ])
    results = asyncio.run(both())
    assert results[0] == results[1]
    assert len(repo.handle("evidence", {"action": "read_blackboard", "problem_id": "problem-1"})["entries"]) == 1
    repo.close()


@pytest.mark.parametrize("stale_refs", [None, [{"entry_id": "obs", "revision": 1}]])
def test_review_skips_stale_first_hypothesis_for_current_context(stale_refs) -> None:
    observation = _entry("obs", "observation", {
        "records": [{"name": "request_latency_p95_ms", "value": 720}],
    }, source="evidence", revision=2)
    stale = _entry("a-stale", "hypothesis", {
        "claim": "Database connection pool exhaustion", "status": "supported",
        "supporting_evidence_ids": ["obs"],
    })
    if stale_refs is not None:
        stale["data"]["observation_refs"] = stale_refs
    fresh = _entry("z-fresh", "hypothesis", {
        "claim": "The available evidence is insufficient to identify the upstream cause",
        "status": "unresolved", "supporting_evidence_ids": ["obs"],
        "open_questions": [], "observation_refs": [{"entry_id": "obs", "revision": 2}],
    })
    board = FakeBlackboard("review", "missing_evidence", [observation, stale, fresh])
    asyncio.run(SpecialistRunner("review", blackboard=board, model=OfflineFixtureAdapter()).run({
        "problem_id": "problem-1", "purpose": "review_hypotheses", "dispatch_id": "fresh-review",
    }))
    reviews = [entry for entry in board.entries if entry["knowledge_type"] == "review"]
    assert len(reviews) == 1
    assert reviews[0]["data"]["target_entry_id"] == "z-fresh"


def test_live_initial_baseline_is_bounded_grounded_and_replayable():
    board = FakeBlackboard('evidence', 'db_pool')
    model = ScriptModel([])
    model.mode = 'live'
    runner = SpecialistRunner('evidence', blackboard=board, model=model)
    payload = {'problem_id': 'problem-1', 'purpose': 'collect_evidence', 'dispatch_id': 'baseline'}
    first = asyncio.run(runner.run(payload))
    second = asyncio.run(runner.run(payload))
    assert first['entry_ids'] == second['entry_ids']
    assert len(board.entries) == 1
    data = board.entries[0]['data']
    assert {item['tool_name'] for item in data['components']} == {'query_metrics', 'query_logs', 'get_change_history'}
    assert len(data['source_ids']) == len(data['records'])
    assert any(row.get('name') == 'db_pool_max_connections' for row in data['records'])
    assert any('message' in row for row in data['records'])
    assert model.call_count == 0


def test_generic_upstream_errors_do_not_establish_a_cause():
    obs = _entry('obs', 'observation', {'records': [
        {'name': 'request_latency_p95_ms', 'value': 720},
        {'message': 'Request failed with an upstream error', 'fields': {'status': 502}},
    ]}, source='evidence')
    claim = 'Intermittent upstream dependency failures are causing 502 errors, elevated error rate, and high latency.'
    with pytest.raises(ValueError, match='cause-specific'):
        _validate_diagnosis({'hypotheses': [{'claim': claim, 'status': 'supported', 'supporting_evidence_ids': ['obs']}], 'evidence_requests': []}, [obs], 'diagnosis')
    target = _entry('hyp', 'hypothesis', {'claim': claim, 'status': 'supported', 'supporting_evidence_ids': ['obs']})
    with pytest.raises(ValueError, match='cannot accept'):
        _validate_review({'target_entry_id': 'hyp', 'target_revision': 1, 'verdict': 'accept', 'rationale': '502 indicates dependency failure', 'supporting_evidence_ids': ['obs'], 'required_follow_up': None}, target, [obs], 'review_hypotheses', set())


def test_reviewer_receives_tool_contract_and_repairs_invalid_arguments():
    class RepairModel(ScriptModel):
        async def complete(self, **kwargs):
            assert 'service' not in kwargs['context']['available_tools']['query_metrics']
            if self.call_count:
                assert 'permits only' in kwargs['context']['repair_feedback']['validation_error']
            return await super().complete(**kwargs)
    obs = _entry('obs', 'observation', {'records': [{'name': 'request_latency_p95_ms', 'value': 720}]}, source='evidence')
    hyp = _entry('hyp', 'hypothesis', {'claim': 'Database pool issue', 'status': 'proposed', 'supporting_evidence_ids': [], 'observation_refs': [{'entry_id': 'obs', 'revision': 1}]})
    base = {'target_entry_id': 'hyp', 'target_revision': 1, 'verdict': 'request_evidence', 'rationale': 'Need direct measurements', 'supporting_evidence_ids': ['obs']}
    follow = {'question': 'Pool occupancy?', 'requested_tool': 'query_metrics', 'reason': 'Distinguish a cause', 'query': {'service': 'lab', 'metric_names': ['db_pool_active_connections']}}
    model = RepairModel([{**base, 'required_follow_up': follow}, {**base, 'required_follow_up': {**follow, 'query': {'metric_names': ['db_pool_active_connections']}}}])
    board = FakeBlackboard('review', 'missing_evidence', [obs, hyp])
    result = asyncio.run(SpecialistRunner('review', blackboard=board, model=model).run({'problem_id': 'problem-1', 'purpose': 'review_hypotheses', 'dispatch_id': 'repair'}))
    assert result['ok']
    assert model.call_count == 2
    request = next(e for e in board.entries if e['knowledge_type'] == 'evidence_request')
    assert 'service' not in request['data']['query']


def test_solution_review_bounds_derived_runbook_query():
    target = _entry('solution', 'solution', {
        'summary': 'Campus DNS resolution failure. ' * 30,
        'actions': [{'step': 'Check resolver configuration ' * 30, 'purpose': 'Diagnose', 'expected_check': 'Resolution recovers'}],
        'evidence_ids': [], 'runbook_ids': [],
    }, source='response')
    board = FakeBlackboard('review', 'dns_fault', [target])
    model = ScriptModel([{'target_entry_id': 'solution', 'target_revision': 1, 'verdict': 'reject', 'rationale': 'Missing supporting observations', 'supporting_evidence_ids': [], 'required_follow_up': None}])
    result = asyncio.run(SpecialistRunner('review', blackboard=board, model=model).run({'problem_id': 'problem-1', 'purpose': 'review_solution', 'dispatch_id': 'long-runbook-query'}))
    assert result['ok']
    assert model.call_count == 1


def test_live_evidence_executes_approved_request_without_model_copying_it():
    board = FakeBlackboard('evidence', 'missing_evidence', [
        _entry('request', 'evidence_request', {'question': 'Dependency metric?', 'requested_tool': 'query_metrics', 'query': {'metric_names': ['upstream_dependency_errors']}, 'reason': 'Need causal evidence', 'status': 'open'}, source='review'),
    ])
    model = ScriptModel([])
    model.mode = 'live'
    result = asyncio.run(SpecialistRunner('evidence', blackboard=board, model=model).run({'problem_id': 'problem-1', 'purpose': 'collect_evidence', 'dispatch_id': 'exact-request'}))
    assert result['ok']
    assert model.call_count == 0
    assert board.entries[-1]['data']['fulfilled_request_entry_id'] == 'request'
    assert board.entries[-1]['data']['fulfilled_request_status'] == 'unavailable'
