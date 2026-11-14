from __future__ import annotations

from scripts.evaluate import assess
import copy
import pytest


def test_dispatch_limit_check_uses_persisted_non_default_case_limit() -> None:
    snapshot = {
        "problem": {"limits": {"max_dispatches": 13}},
        "entries": [
            {
                "knowledge_type": "controller_decision",
                "data": {"event_type": "dispatch_started", "dispatch_id": f"dispatch-{index}"},
            }
            for index in range(13)
        ],
    }

    result = assess("custom_case", snapshot, {"status": "unresolved"}, {"ground_truth": "unmatched"})

    assert result["dispatch_count"] == 13
    assert result["checks"]["dispatch_limit_respected"] is True


def missing_snapshot() -> dict:
    return {"problem": {}, "entries": [
        {"entry_id": "request-1", "knowledge_type": "evidence_request", "data": {
            "requested_tool": "query_metrics", "query": {"metric_names": ["db_pool_active"]},
        }},
        {"entry_id": "observation-1", "knowledge_type": "observation", "data": {
            "tool_name": "query_metrics", "query": {"metric_names": ["db_pool_active"]},
            "records": [], "fulfilled_request_entry_id": "request-1",
            "fulfilled_request_status": "unavailable",
        }},
    ]}


def test_missing_evidence_blocked_without_evidence_is_operational_failure() -> None:
    result = assess("missing_evidence", {"entries": []}, {
        "status": "blocked", "result": {"reason": "specialist_failed"},
    }, {})
    assert result["operational_failure"] is True
    assert result["checks"]["terminal_status_matches_case_expectation"] is False
    assert result["checks"]["missing_evidence_ends_without_accepted_cause"] is False


def test_missing_evidence_unavailable_unresolved_is_expected() -> None:
    result = assess("missing_evidence", missing_snapshot(), {
        "status": "unresolved", "result": {"reason": "hypotheses_rejected_without_new_evidence"},
    }, {})
    assert result["operational_failure"] is False
    assert result["checks"]["terminal_status_matches_case_expectation"] is True
    assert result["checks"]["missing_evidence_ends_without_accepted_cause"] is True


@pytest.mark.parametrize("defect", ["reason", "request", "query", "records", "solution", "accepted_cause", "blocked"])
def test_missing_evidence_requires_actual_evidence_limited_stop(defect: str) -> None:
    snapshot = copy.deepcopy(missing_snapshot())
    run = {"status": "unresolved", "result": {"reason": "hypotheses_rejected_without_new_evidence"}}
    if defect == "reason":
        run["result"] = {}
    elif defect == "request":
        snapshot["entries"].pop(0)
    elif defect == "query":
        snapshot["entries"][1]["data"]["query"] = {"metric_names": ["other"]}
    elif defect == "records":
        snapshot["entries"][1]["data"]["records"] = [{"value": 1}]
    elif defect == "solution":
        snapshot["entries"].append({"entry_id": "solution-1", "knowledge_type": "solution", "data": {}})
    elif defect == "accepted_cause":
        snapshot["entries"].extend([
            {"entry_id": "hypothesis-1", "revision": 1, "knowledge_type": "hypothesis", "data": {"status": "supported"}},
            {"entry_id": "review-1", "knowledge_type": "review", "data": {
                "target_entry_id": "hypothesis-1", "target_revision": 1, "verdict": "accept",
            }},
        ])
    else:
        run["status"] = "blocked"
    result = assess("missing_evidence", snapshot, run, {})
    assert result["checks"]["missing_evidence_ends_without_accepted_cause"] is False


@pytest.mark.parametrize("case_id", ["db_pool", "dns_fault"])
def test_completed_case_status_expectations_remain_unchanged(case_id: str) -> None:
    result = assess(case_id, {"entries": []}, {"status": "completed"}, {})
    assert result["checks"]["terminal_status_matches_case_expectation"] is True
    assert result["operational_failure"] is False
    assert "missing_evidence_ends_without_accepted_cause" not in result["checks"]


def test_dispatch_limit_check_falls_back_to_current_configured_limit() -> None:
    snapshot = {
        "problem": {},
        "entries": [
            {
                "knowledge_type": "controller_decision",
                "data": {"event_type": "dispatch_started", "dispatch_id": f"dispatch-{index}"},
            }
            for index in range(13)
        ],
    }

    result = assess(
        "custom_case",
        snapshot,
        {"status": "unresolved"},
        {"ground_truth": "unmatched"},
        configured_max_dispatches=13,
    )

    assert result["checks"]["dispatch_limit_respected"] is True


@pytest.mark.parametrize("observation_query, expected", [
    ({"metric_names": ["db_pool_active"], "contains": None}, True),
    ({"metric_names": ["db_pool_active"], "contains": "other"}, False),
    ({"metric_names": ["other"], "contains": None}, False),
    ({"metric_names": ["db_pool_active"], "contains": 7}, False),
])
def test_missing_evidence_query_defaults_are_normalized_without_losing_filters(observation_query, expected):
    snapshot = missing_snapshot()
    snapshot["entries"][1]["data"]["query"] = observation_query
    result = assess("missing_evidence", snapshot, {
        "status": "unresolved", "result": {"reason": "hypotheses_rejected_without_new_evidence"},
    }, {})
    assert result["checks"]["missing_evidence_unavailable_request_recorded"] is expected
    assert result["checks"]["missing_evidence_ends_without_accepted_cause"] is expected
