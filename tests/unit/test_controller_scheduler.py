from __future__ import annotations

from campus_blackboard.controller.scheduler import DispatchPlan, TerminalPlan, choose_next_action


def entry(entry_id: str, kind: str, data: dict, *, revision: int = 1, created_at: str = "2026-11-06T00:00:00Z") -> dict:
    if kind == "hypothesis":
        data = {"observation_refs": [
            {"entry_id": ref, "revision": 1}
            for ref in dict.fromkeys(data.get("supporting_evidence_ids", []) + data.get("contradicting_evidence_ids", []))
        ], **data}
    return {
        "entry_id": entry_id,
        "problem_id": "problem-1",
        "knowledge_type": kind,
        "source_agent": "test",
        "revision": revision,
        "data": data,
        "created_at": created_at,
    }


def snapshot(*entries: dict, board_revision: int = 1) -> dict:
    return {
        "problem": {"problem_id": "problem-1", "case_id": "db_pool", "board_revision": board_revision},
        "entries": list(entries),
        "history": [],
    }


def test_empty_board_dispatches_evidence_without_assuming_a_diagnosis() -> None:
    result = choose_next_action(snapshot())
    assert isinstance(result, DispatchPlan)
    assert (result.role, result.purpose) == ("evidence", "collect_evidence")
    assert result.trigger["rule"] == "no_observations"


def test_open_evidence_request_precedes_diagnosis() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_metrics"}),
            entry("request-1", "evidence_request", {"question": "What is pool saturation?", "status": "open"}),
        )
    )
    assert isinstance(result, DispatchPlan)
    assert result.role == "evidence"
    assert result.trigger["rule"] == "open_evidence_request"


def test_unavailable_requested_evidence_is_not_retried_forever() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"fulfilled_request_entry_id": "request-1", "fulfilled_request_status": "unavailable"}),
            entry("request-1", "evidence_request", {"question": "Missing counter", "status": "open"}),
        )
    )
    assert isinstance(result, DispatchPlan)
    assert result.role == "diagnosis"
    assert result.trigger["rule"] == "no_hypotheses"


def test_hypothesis_is_reviewed_at_its_exact_current_revision() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_metrics"}),
            entry("hyp-1", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}, revision=2),
            entry("review-1", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}),
        )
    )
    assert isinstance(result, DispatchPlan)
    assert result.role == "review"
    assert result.purpose == "review_hypotheses"
    assert result.trigger["hypothesis"] == {"entry_id": "hyp-1", "revision": 2}


def test_new_unconsidered_observation_causes_diagnosis_revision() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_metrics"}),
            entry("obs-2", "observation", {"tool_name": "query_logs"}),
            entry("hyp-1", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}),
            entry("review-1", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}),
        )
    )
    assert isinstance(result, DispatchPlan)
    assert result.role == "diagnosis"
    assert result.trigger["rule"] == "new_observation_not_considered"
    assert result.trigger["uncovered_observation_ids"] == ["obs-2"]


def test_acceptance_of_non_supported_hypothesis_does_not_authorize_response() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_metrics"}),
            entry("hyp-1", "hypothesis", {"status": "weakened", "supporting_evidence_ids": ["obs-1"]}),
            entry("review-1", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}),
        )
    )
    assert isinstance(result, TerminalPlan)
    assert result.status == "unresolved"
    assert result.reason == "no_supported_reviewed_hypothesis"


def test_latest_review_verdict_wins_for_same_exact_revision() -> None:
    result = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_metrics"}),
            entry("hyp-1", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}),
            entry("review-accept", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}, created_at="2026-11-06T00:00:00Z"),
            entry("review-reject", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "reject"}, created_at="2026-11-06T00:01:00Z"),
        )
    )
    assert isinstance(result, DispatchPlan)
    assert result.role == "diagnosis"
    assert result.trigger["rule"] == "review_rejected_hypothesis"


def test_dispatch_signature_prevents_duplicate_role_turn() -> None:
    plan = choose_next_action(snapshot())
    assert isinstance(plan, DispatchPlan)
    seen = entry("decision-1", "controller_decision", {
        "event_type": "dispatch_started",
        "dispatch_key": plan.dispatch_key,
    })
    result = choose_next_action(snapshot(seen))
    assert isinstance(result, TerminalPlan)
    assert result.reason == "no_observations_available"


def test_completed_requires_current_supported_reviewed_hypothesis_and_cited_solution() -> None:
    completed = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_logs"}),
            entry("hyp-1", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}),
            entry("hyp-review", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}),
            entry("solution-1", "solution", {"summary": "Inspect the pool", "evidence_ids": ["obs-1"], "runbook_ids": ["database-connection-pool"]}),
            entry("solution-review", "review", {"target_entry_id": "solution-1", "target_revision": 1, "verdict": "accept"}),
        )
    )
    assert isinstance(completed, TerminalPlan)
    assert completed.status == "completed"

    stale = choose_next_action(
        snapshot(
            entry("obs-1", "observation", {"tool_name": "query_logs"}),
            entry("hyp-1", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}, revision=2),
            entry("hyp-review", "review", {"target_entry_id": "hyp-1", "target_revision": 1, "verdict": "accept"}),
            entry("solution-1", "solution", {"evidence_ids": ["obs-1"], "runbook_ids": ["database-connection-pool"]}),
            entry("solution-review", "review", {"target_entry_id": "solution-1", "target_revision": 1, "verdict": "accept"}),
        )
    )
    assert isinstance(stale, DispatchPlan)
    assert stale.role == "review"
    assert stale.trigger["hypothesis"]["revision"] == 2


def test_dispatch_limit_ends_as_unresolved() -> None:
    result = choose_next_action(snapshot(), dispatch_count=12, max_dispatches=12)
    assert isinstance(result, TerminalPlan)
    assert (result.status, result.reason) == ("unresolved", "dispatch_limit_reached")


def test_completed_board_can_finish_at_dispatch_limit() -> None:
    board = _completed_board()
    result = choose_next_action(board, dispatch_count=9, max_dispatches=9)
    assert result == TerminalPlan("completed", "current_supported_diagnosis_and_solution_accepted")


def _completed_board() -> dict:
    return snapshot(
        entry("obs-1", "observation", {"records": [{"value": 100}]}),
        entry("h", "hypothesis", {"status": "supported", "supporting_evidence_ids": ["obs-1"]}),
        entry("hr", "review", {"target_entry_id": "h", "target_revision": 1, "verdict": "accept"}),
        entry("s", "solution", {"evidence_ids": ["obs-1"], "runbook_ids": ["database-connection-pool"]}),
        entry("sr", "review", {"target_entry_id": "s", "target_revision": 1, "verdict": "accept"}),
    )


def test_changed_observation_invalidates_existing_accepted_diagnosis() -> None:
    board = _completed_board()
    board["entries"][0].update(revision=2, data={"records": [{"value": 0}]})
    plan = choose_next_action(board)
    assert isinstance(plan, DispatchPlan)
    assert plan.role == "diagnosis"
    assert plan.trigger["rule"] == "observation_revision_changed"
    board["entries"].append(entry("d", "controller_decision", {
        "event_type": "dispatch_started", "dispatch_key": plan.dispatch_key,
    }))
    assert choose_next_action(board) == TerminalPlan("unresolved", "diagnosis_has_stale_evidence")


def test_legacy_hypothesis_without_evidence_versions_requires_reanalysis() -> None:
    board = _completed_board()
    board["entries"][1]["data"].pop("observation_refs")
    result = choose_next_action(board)
    assert isinstance(result, DispatchPlan)
    assert result.role == "diagnosis"


def test_fresh_unresolved_diagnosis_does_not_reuse_stale_supported_cause() -> None:
    board = _completed_board()
    board["entries"][0]["revision"] = 2
    board["entries"].extend([
        entry("new-h", "hypothesis", {
            "status": "unresolved", "supporting_evidence_ids": ["obs-1"],
            "observation_refs": [{"entry_id": "obs-1", "revision": 2}],
        }),
        entry("new-review", "review", {"target_entry_id": "new-h", "target_revision": 1, "verdict": "accept"}),
    ])
    result = choose_next_action(board)
    assert isinstance(result, TerminalPlan)
    assert result.status == "unresolved"
    assert result.reason == "accepted_solution_has_incomplete_references"


def test_supported_hypothesis_review_precedes_optional_diagnosis_request():
    obs = entry('obs', 'observation', {'tool_name': 'query_metrics'})
    hyp = entry('hyp', 'hypothesis', {'status': 'supported', 'supporting_evidence_ids': ['obs']})
    request = entry('optional', 'evidence_request', {'requested_tool': 'query_logs', 'query': {'contains': 'timeout'}, 'status': 'open'})
    request['source_agent'] = 'diagnosis'
    result = choose_next_action(snapshot(obs, hyp, request))
    assert isinstance(result, DispatchPlan)
    assert result.role == 'review'


def test_reviewer_request_still_precedes_supported_hypothesis_review():
    obs = entry('obs', 'observation', {'tool_name': 'query_metrics'})
    hyp = entry('hyp', 'hypothesis', {'status': 'supported', 'supporting_evidence_ids': ['obs']})
    request = entry('required', 'evidence_request', {'requested_tool': 'query_logs', 'query': {'contains': 'timeout'}, 'status': 'open'})
    request['source_agent'] = 'review'
    result = choose_next_action(snapshot(obs, hyp, request))
    assert isinstance(result, DispatchPlan)
    assert result.role == 'evidence'


def test_equivalent_completed_query_does_not_trigger_another_evidence_task():
    obs = entry('obs', 'observation', {'tool_name': 'query_logs', 'query': {'contains': 'timeout error', 'service': None, 'level': None, 'limit': 20}})
    req = entry('again', 'evidence_request', {'requested_tool': 'query_logs', 'query': {'contains': 'error|timeout'}, 'status': 'open'})
    result = choose_next_action(snapshot(obs, req))
    assert isinstance(result, DispatchPlan)
    assert result.role == 'diagnosis'


def test_accepted_supported_diagnosis_reaches_response_without_optional_work():
    obs = entry('obs', 'observation', {'tool_name': 'query_metrics'})
    hyp = entry('hyp', 'hypothesis', {'status': 'supported', 'supporting_evidence_ids': ['obs']})
    alternative = entry('alt', 'hypothesis', {'status': 'rejected', 'supporting_evidence_ids': [], 'observation_refs': [{'entry_id': 'obs', 'revision': 1}]})
    review = entry('review', 'review', {'target_entry_id': 'hyp', 'target_revision': 1, 'verdict': 'accept'})
    request = entry('optional', 'evidence_request', {'requested_tool': 'query_logs', 'query': {'contains': 'timeout'}, 'status': 'open'})
    request['source_agent'] = 'diagnosis'
    result = choose_next_action(snapshot(obs, hyp, alternative, review, request))
    assert isinstance(result, DispatchPlan)
    assert result.role == 'response'


def test_two_empty_attempts_stop_only_after_diagnosis_considers_them():
    first = entry('empty-1', 'observation', {'records': [], 'fulfilled_request_status': 'unavailable'}, created_at='2026-11-06T00:00:01Z')
    second = entry('empty-2', 'observation', {'records': [], 'fulfilled_request_status': 'unavailable'}, created_at='2026-11-06T00:00:02Z')
    refs = [{'entry_id': 'empty-1', 'revision': 1}, {'entry_id': 'empty-2', 'revision': 1}]
    hyp = entry('hyp', 'hypothesis', {'status': 'unresolved', 'observation_refs': refs})
    result = choose_next_action(snapshot(first, second, hyp))
    assert isinstance(result, TerminalPlan)
    assert result.reason == 'repeated_queries_returned_no_evidence'
    hyp['data']['observation_refs'] = refs[:1]
    assert isinstance(choose_next_action(snapshot(first, second, hyp)), DispatchPlan)
    hyp['data']['observation_refs'] = refs
    hyp['data']['status'] = 'supported'
    assert isinstance(choose_next_action(snapshot(first, second, hyp)), DispatchPlan)
