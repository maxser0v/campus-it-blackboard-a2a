"""Presentation keeps incident data safe, review-specific, and read-only."""
from dataclasses import replace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from campus_blackboard import dashboard_ui as ui


def test_latest_review_never_approves_a_different_revision():
    hypothesis = {"entry_id": "hypothesis-1", "revision": 2}
    reviews = [
        {"created_at": "03", "data": {"target_entry_id": "hypothesis-1", "target_revision": 1, "verdict": "accept"}},
        {"created_at": "01", "data": {"target_entry_id": "hypothesis-1", "target_revision": 2, "verdict": "request_evidence"}},
        {"created_at": "02", "data": {"target_entry_id": "hypothesis-1", "target_revision": 2, "verdict": "reject"}},
    ]
    assert ui.data(ui.latest_review(hypothesis, reviews))["verdict"] == "reject"
    assert ui.latest_review({"entry_id": "hypothesis-2", "revision": 1}, reviews) is None


def test_untrusted_incident_fields_are_escaped_in_custom_html():
    attack = '<img src=x onerror="alert(1)">'
    assert "<img" not in ui.pill(attack, attack)
    table = ui.record_table([{"name": attack, "value": attack, "unit": attack}])
    assert "<img" not in table
    assert "&lt;img" in table
    assert '<th scope="col">' in table


def test_linked_evidence_outcome_overrides_open_request():
    request = {"entry_id": "request-1", "data": {"status": "open"}}
    observation = {"knowledge_type": "observation", "data": {"fulfilled_request_entry_id": "request-1", "fulfilled_request_status": "unavailable"}}
    assert ui.evidence_request_status(request, [observation]) == "unavailable"
    assert ui.record_table([]).find("no records") >= 0


def sample_board():
    return {"problem": {"title": "Portal investigation", "board_revision": 8}, "entries": [
        {"entry_id": "obs-1", "knowledge_type": "observation", "source_agent": "evidence", "revision": 1,
         "data": {"tool_name": "query_metrics", "rationale": "Measure the pool.", "records": [{"name": "pool_usage", "value": 40, "unit": "connections"}]}},
        {"entry_id": "hyp-1", "knowledge_type": "hypothesis", "source_agent": "diagnosis", "revision": 2,
         "data": {"claim": "The pool is constrained.", "status": "supported", "supporting_evidence_ids": ["obs-1"]}},
        {"entry_id": "review-1", "knowledge_type": "review", "source_agent": "review", "revision": 1,
         "data": {"target_entry_id": "hyp-1", "target_revision": 2, "verdict": "accept", "rationale": "The observed pool is full."}},
        {"entry_id": "solution-1", "knowledge_type": "solution", "source_agent": "response", "revision": 1,
         "data": {"summary": "Inspect connection handling.", "actions": [{"step": "Check active sessions", "purpose": "Locate blocked work", "expected_check": "Sessions accounted for"}], "evidence_ids": ["obs-1"], "runbook_ids": ["pool-runbook"]}},
    ]}


def test_every_view_renders_real_data_and_filters_without_starting_work():
    with patch("campus_blackboard.dashboard._get", return_value={"status": "completed", "result": {"dispatch_count": 4}}), patch(
        "campus_blackboard.dashboard._read_board", return_value=sample_board()
    ), patch("campus_blackboard.dashboard._post") as post:
        app = AppTest.from_string("from campus_blackboard.dashboard import _render_board\n_render_board('test-run')").run()
        assert not app.exception
        assert len(app.tabs) == 6
        assert any("40" in item.value and "connections" in item.value for item in app.tabs[0].markdown)
        assert any("The observed pool is full." in item.value for item in app.tabs[1].markdown)
        assert any("Sessions accounted for" in item.value for item in app.tabs[4].markdown)
        app.selectbox[0].select("Evidence").run()
        assert not app.exception
        assert app.selectbox[0].value == "Evidence"
        post.assert_not_called()


def test_start_button_creates_exactly_one_run_and_opens_workspace():
    from campus_blackboard.dashboard import settings

    def controller_get(path):
        if path == "/cases":
            return [{"case_id": "db_pool", "title": "Portal latency", "symptoms": "Pages are slow."}]
        return {"status": "completed", "result": {"dispatch_count": 4}}

    with patch("campus_blackboard.dashboard.settings", replace(settings, model_mode="offline")), patch(
        "campus_blackboard.dashboard._get", side_effect=controller_get
    ), patch("campus_blackboard.dashboard._read_board", return_value=sample_board()), patch(
        "campus_blackboard.dashboard._post", return_value={"problem_id": "new-run"}
    ) as post:
        app = AppTest.from_string("from campus_blackboard.dashboard import main\nmain()").run()
        assert not app.exception
        assert not app.tabs
        app.button[0].click().run()
        assert not app.exception
        assert app.session_state["problem_id"] == "new-run"
        assert len(app.tabs) == 6
        assert post.call_count == 1
        assert post.call_args.args[1]["case_id"] == "db_pool"
