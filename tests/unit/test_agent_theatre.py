"""The presentation must reflect recorded events and safely embed incident text."""
from campus_blackboard.agent_theatre import theatre_events, theatre_html


def entry(kind, data, source="controller", time="2026-11-21T10:00:00Z"):
    return {"knowledge_type": kind, "data": data, "source_agent": source, "created_at": time, "entry_id": "entry-1"}


def test_projects_history_in_order_including_failures_and_revisions():
    history = [
        entry("controller_decision", {"event_type": "dispatch_finished", "agent": "review", "dispatch_id": "d1", "status": "failed", "error": "Transport failed"}, time="03"),
        entry("hypothesis", {"claim": "Revised claim"}, "diagnosis", "02"),
        entry("controller_decision", {"event_type": "dispatch_started", "agent": "review", "dispatch_id": "d1", "purpose": "review_hypothesis"}, time="01"),
        entry("controller_decision", {"event_type": "terminal_decision", "status": "blocked", "reason": "transport_failure"}, time="04"),
    ]
    events = theatre_events({"history": history, "entries": [history[1]]})
    assert [e["phase"] for e in events] == ["started", "published", "failed", "terminal"]
    assert events[0]["dispatch"] == "d1"
    assert events[1]["detail"] == "Revised claim"
    assert events[2]["detail"] == "Transport failed"
    assert events[3]["role"] == "controller"


def test_fallback_ignores_unknown_sources_and_invalid_data():
    assert theatre_events({"entries": [entry("observation", {}, "unknown"), entry("review", []), entry("review", {"verdict": "supported"}, "review")]})[0]["detail"] == "supported"
    assert theatre_events({}) == []


def test_incident_text_cannot_break_out_of_script():
    attack = '</script><script>alert("incident")</script>'
    html = theatre_html({"entries": [entry("observation", {"rationale": attack}, "evidence")]}, {"status": "completed"}, "demo")
    assert attack not in html
    assert "\\u003c/script\\u003e" in html
    assert "__THEATRE_DATA__" not in html


def test_terminal_notices_do_not_move_tabs_and_reset_the_selected_view():
    """Streamlit identifies the tabs by their position during fragment refreshes."""
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest

    positions = []
    for status in ("investigating", "completed", "blocked", "unresolved"):
        with patch("campus_blackboard.dashboard._get", return_value={"status": status}), patch(
            "campus_blackboard.dashboard._read_board", return_value={}
        ):
            app = AppTest.from_string(
                "from campus_blackboard.dashboard import _render_board\n_render_board('verification')"
            ).run()
        assert not app.exception
        assert [tab.label for tab in app.tabs][-1] == "Agent theatre"
        positions.append(next(index for index, child in app.main.children.items() if child.type == "tab_container"))
    assert len(set(positions)) == 1


def test_work_log_preserves_complete_public_explanations_and_tool_results():
    explanation = "Explain the selected metric. " * 100
    observed = {"rationale": explanation, "tool_name": "query_metrics", "query": {"metric": "pool"},
                "records": [{"name": "pool_usage", "value": 98}], "source_ids": ["metrics-1"],
                "dispatch_result": {"dispatch_id": "task-1"}, "private_reasoning": "Do not expose this"}
    event = theatre_events({"entries": [entry("observation", observed, "evidence")]})[0]
    sections = {section["label"]: section["text"] for section in event["sections"]}
    assert sections["Why this evidence was collected"] == explanation
    assert '"value": 98' in sections["Observed results"]
    assert "metrics-1" in sections["Sources"]
    assert event["dispatch"] == "task-1"
    assert "Do not expose this" not in str(event)


def test_work_log_shows_review_explanation_and_exact_target_revision():
    review = {"verdict": "request_evidence", "rationale": "Pool usage alone does not exclude a leaked connection.",
              "target_entry_id": "hypothesis-1", "target_revision": 2,
              "supporting_evidence_ids": ["observation-1"],
              "required_follow_up": {"question": "Are connections being returned?"}}
    event = theatre_events({"entries": [{**entry("review", review, "review"), "revision": 3}]})[0]
    sections = {section["label"]: section["text"] for section in event["sections"]}
    assert sections["Published review explanation"] == review["rationale"]
    assert sections["Reviewed revision"] == "2"
    assert "Are connections being returned?" in sections["Required follow-up"]
    assert event["revision"] == 3


def test_work_log_records_controller_trigger_and_ordered_response_checks():
    history = [entry("controller_decision", {"event_type": "dispatch_started", "agent": "response",
                "purpose": "propose_response", "trigger": {"accepted_hypothesis": "hypothesis-1"}}),
               entry("solution", {"summary": "Review connection use", "actions": [
                   {"step": "Inspect long-lived sessions", "purpose": "Find unreleased connections", "expected_check": "Idle sessions identified"},
                   {"step": "Review limits", "purpose": "Confirm capacity", "expected_check": "Limits documented"}],
                   "caveats": ["Approval required"], "runbook_ids": ["pool-runbook"]}, "response")]
    first, second = theatre_events({"history": history})
    assert "hypothesis-1" in str(first["sections"])
    sections = {section["label"]: section["text"] for section in second["sections"]}
    assert sections["Recovery steps and expected checks"].startswith("1. Inspect long-lived sessions")
    assert "2. Review limits" in sections["Recovery steps and expected checks"]
    assert "Expected check: Limits documented" in sections["Recovery steps and expected checks"]
    assert "Approval required" in sections["Limitations and caveats"]
