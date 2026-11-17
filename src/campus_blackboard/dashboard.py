"""Live Streamlit view for an investigation; refreshes never start work."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import streamlit as st

from campus_blackboard.config import get_settings
from campus_blackboard.blackboard.client import BlackboardClient


settings = get_settings()
CONTROLLER_URL = f"http://{settings.bind_host}:{settings.controller_port}"
BLACKBOARD_URL = f"http://{settings.bind_host}:{settings.blackboard_port}"
REFRESH_SECONDS = 3
board_client = BlackboardClient(
    BLACKBOARD_URL,
    actor="dashboard",
    api_key=settings.agent_credentials.get("dashboard"),
    timeout=settings.request_timeout,
)


def _get(path: str, *, timeout: float = 5.0) -> Any:
    response = httpx.get(f"{CONTROLLER_URL}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def _post(path: str, body: dict[str, Any], *, timeout: float = 10.0) -> Any:
    response = httpx.post(f"{CONTROLLER_URL}{path}", json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _read_board(problem_id: str) -> dict[str, Any]:
    return asyncio.run(board_client.call("read_blackboard", problem_id=problem_id))


def _data(entry: dict[str, Any]) -> dict[str, Any]:
    data = entry.get("data", {})
    return data if isinstance(data, dict) else {}


def _evidence_label(entry_id: str, entries: dict[str, dict[str, Any]]) -> str:
    entry = entries.get(entry_id)
    if not entry:
        return f"{entry_id} (not found on this board)"
    data = _data(entry)
    tool = data.get("tool_name", "observation")
    records = data.get("records", [])
    summary = ", ".join(
        f"{r.get('name', 'value')}={r.get('value', '')}{r.get('unit', '')}"
        for r in records[:3]
        if isinstance(r, dict)
    )
    return f"{entry_id} · {tool}" + (f" · {summary}" if summary else "")


def _evidence_request_status(request: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    """Show the result of a linked tool attempt instead of stale request state."""
    request_id = str(request.get("entry_id", ""))
    linked_statuses = {
        str(_data(entry).get("fulfilled_request_status", "")).lower()
        for entry in entries
        if entry.get("knowledge_type") == "observation"
        and str(_data(entry).get("fulfilled_request_entry_id", "")) == request_id
    }
    if "fulfilled" in linked_statuses:
        return "fulfilled"
    if "unavailable" in linked_statuses:
        return "unavailable"
    return str(_data(request).get("status", "open"))


def _render_board(problem_id: str) -> None:
    try:
        status = _get(f"/runs/{problem_id}")
        board = _read_board(problem_id)
    except Exception as exc:
        st.error(f"The controller or Blackboard could not be reached: {exc}")
        return

    problem = board.get("problem", {})
    entries = board.get("entries", [])
    history = board.get("history", [])
    entry_map = {str(e.get("entry_id")): e for e in entries if e.get("entry_id")}
    activity = sorted(history or entries, key=lambda e: str(e.get("created_at", "")))

    col_status, col_revision, col_runtime = st.columns(3)
    col_status.metric("Investigation", str(status.get("status", "unknown")).replace("_", " ").title())
    col_revision.metric("Blackboard revision", problem.get("board_revision", "—"))
    result = status.get("result") or {}
    col_runtime.metric("Specialist dispatches", result.get("dispatch_count", "in progress"))
    if status.get("status") == "blocked":
        st.error(f"The investigation stopped because of an operational failure: {(result or {}).get('error') or (result or {}).get('reason', 'unknown error')}.")
    elif status.get("status") == "unresolved":
        st.warning(f"The evidence did not support a completed response. Reason: {(result or {}).get('reason', 'unresolved')}.")
    elif status.get("status") == "completed":
        st.success("The current diagnosis and response proposal passed review. No remediation was executed.")

    observations = [e for e in entries if e.get("knowledge_type") == "observation"]
    hypotheses = [e for e in entries if e.get("knowledge_type") == "hypothesis"]
    reviews = [e for e in entries if e.get("knowledge_type") == "review"]
    solutions = [e for e in entries if e.get("knowledge_type") == "solution"]
    decisions = [e for e in history if e.get("knowledge_type") == "controller_decision"]

    tabs = st.tabs(["Observations", "Hypotheses", "Activity", "Controller decisions", "Response"])
    with tabs[0]:
        if not observations:
            st.info("The Evidence Agent has not added observations yet.")
        for observation in observations:
            data = _data(observation)
            with st.container(border=True):
                st.markdown(f"**{data.get('tool_name', 'Observation')}** · {observation.get('entry_id')} · by {observation.get('source_agent')}")
                st.caption(f"Revision {observation.get('revision')} · {data.get('observed_at', observation.get('created_at', ''))}")
                if data.get("rationale"):
                    st.write(data["rationale"])
                st.json({"query": data.get("query", {}), "source_ids": data.get("source_ids", []), "records": data.get("records", [])}, expanded=False)

    with tabs[1]:
        if not hypotheses:
            st.info("No hypotheses have been recorded yet.")
        for hypothesis in sorted(hypotheses, key=lambda e: str(e.get("entry_id"))):
            data = _data(hypothesis)
            target_reviews = [
                r for r in reviews
                if _data(r).get("target_entry_id") == hypothesis.get("entry_id")
                and _data(r).get("target_revision") == hypothesis.get("revision")
            ]
            verdict = _data(target_reviews[-1]).get("verdict", "not reviewed") if target_reviews else "not reviewed"
            with st.container(border=True):
                st.markdown(f"**{data.get('claim', 'Hypothesis')}** · `{data.get('status', 'unknown')}` · revision {hypothesis.get('revision')}")
                st.caption(f"Latest review of this revision: {verdict}")
                supporting = data.get("supporting_evidence_ids", [])
                contradicting = data.get("contradicting_evidence_ids", [])
                st.write("Supporting observations:")
                for ref in supporting:
                    st.write(f"- {_evidence_label(str(ref), entry_map)}")
                st.write("Contradicting observations:")
                for ref in contradicting:
                    st.write(f"- {_evidence_label(str(ref), entry_map)}")
                if data.get("open_questions"):
                    st.write("Open questions: " + "; ".join(map(str, data["open_questions"])))
                revisions = sorted(
                    (
                        item for item in history
                        if item.get("knowledge_type") == "hypothesis"
                        and item.get("entry_id") == hypothesis.get("entry_id")
                    ),
                    key=lambda item: int(item.get("revision", 1)),
                )
                if len(revisions) > 1:
                    with st.expander(f"Revision history · {len(revisions)} versions"):
                        for revision in revisions:
                            revision_data = _data(revision)
                            current = int(revision.get("revision", 1)) == int(hypothesis.get("revision", 1))
                            st.markdown(
                                f"**Revision {revision.get('revision')}**"
                                + (" · current" if current else "")
                                + f" · {revision_data.get('status', 'unknown')}"
                            )
                            st.write(revision_data.get("claim", ""))
                            supports = revision_data.get("supporting_evidence_ids", [])
                            contradicts = revision_data.get("contradicting_evidence_ids", [])
                            if supports:
                                st.caption("Supporting: " + ", ".join(map(str, supports)))
                            if contradicts:
                                st.caption("Contradicting: " + ", ".join(map(str, contradicts)))
                            if revision_data.get("open_questions"):
                                st.caption("Open questions: " + "; ".join(map(str, revision_data["open_questions"])))
        requests = [e for e in entries if e.get("knowledge_type") == "evidence_request"]
        if requests:
            st.markdown("#### Evidence requests")
            for request in requests:
                status_label = _evidence_request_status(request, entries)
                st.write(f"- **{status_label}**: {_data(request).get('question', '')}")

    with tabs[2]:
        if not activity:
            st.info("Activity will appear here as agents contribute to the Blackboard.")
        for item in reversed(activity):
            kind = item.get("knowledge_type", "entry")
            data = _data(item)
            title = data.get("summary") or data.get("claim") or data.get("question") or data.get("event_type") or kind
            revision = f" · revision {item.get('revision')}" if kind in {"hypothesis", "solution"} else ""
            outcome = data.get("status") or data.get("verdict")
            outcome_text = f" · {outcome}" if outcome else ""
            detail = data.get("rationale") or data.get("reason")
            detail_text = f" — {str(detail)[:240]}" if detail else ""
            st.markdown(f"- `{item.get('created_at', '')}` · **{kind}**{revision}{outcome_text} · {item.get('source_agent', 'unknown')} · {title}{detail_text}")

    with tabs[3]:
        if not decisions:
            st.info("The controller has not made a dispatch decision yet.")
        for decision in sorted(decisions, key=lambda e: str(e.get("created_at", "")), reverse=True):
            data = _data(decision)
            with st.expander(f"{data.get('event_type', 'decision')} · {data.get('agent', data.get('status', 'controller'))} · {data.get('purpose', data.get('reason', ''))}"):
                st.write("Trigger / reason:")
                st.json(data.get("trigger", {"reason": data.get("reason")}), expanded=True)
                if data.get("task_id"):
                    st.caption(f"A2A task ID: {data['task_id']}")
                if data.get("result_entry_ids"):
                    st.caption("Result entries: " + ", ".join(map(str, data["result_entry_ids"])))
                if data.get("error"):
                    st.error(data["error"])

    with tabs[4]:
        if not solutions:
            st.info("A response proposal will appear after a supported diagnosis is reviewed.")
        else:
            solution = sorted(solutions, key=lambda e: (str(e.get("created_at", "")), int(e.get("revision", 1))))[-1]
            data = _data(solution)
            st.markdown(f"### Proposed response · revision {solution.get('revision')}")
            st.write(data.get("summary", ""))
            for index, action in enumerate(data.get("actions", []), start=1):
                if isinstance(action, dict):
                    st.markdown(f"**{index}. {action.get('step', 'Action')}**")
                    st.write(action.get("purpose", ""))
                    if action.get("expected_check"):
                        st.caption("Expected check: " + str(action["expected_check"]))
            if data.get("evidence_ids"):
                st.markdown("**Evidence references**")
                for ref in data["evidence_ids"]:
                    st.write(f"- {_evidence_label(str(ref), entry_map)}")
            if data.get("runbook_ids"):
                st.write("Runbooks: " + ", ".join(map(str, data["runbook_ids"])))
            for caveat in data.get("caveats", []):
                st.warning(str(caveat))
            st.caption("This is a recommendation only; the prototype does not change campus systems.")


def main() -> None:
    st.set_page_config(page_title="Campus Incident Investigator", layout="wide")
    st.title("Campus IT Incident Investigator")
    st.caption("A2A specialists share evidence and revise findings through a persistent Blackboard.")
    if settings.model_mode == "offline":
        st.info("Offline model mode · agent responses are fixture-driven and do not call a live model provider.")
    else:
        model_label = settings.codex_model or "Codex default" if settings.model_provider == "codex_cli" else settings.model_name or "provider default"
        if settings.model_provider == "codex_cli" and settings.codex_reasoning_effort:
            model_label += f" · reasoning: {settings.codex_reasoning_effort}"
        st.info(f"Live model mode · provider: {settings.model_provider} · configured model: {model_label}")

    try:
        _get("/health")
        cases = _get("/cases")
    except Exception as exc:
        st.error(f"Controller API is unavailable at {CONTROLLER_URL}: {exc}")
        st.stop()

    if not cases:
        st.error("No fixture cases are available.")
        st.stop()

    options = {case["case_id"]: case for case in cases}
    selected_case_id = st.selectbox(
        "Investigation case",
        options=list(options),
        format_func=lambda case_id: options[case_id]["title"],
    )
    st.write(options[selected_case_id].get("symptoms", ""))
    if st.button("Start a fresh investigation", type="primary"):
        request_id = str(uuid.uuid4())
        try:
            started = _post("/runs", {"case_id": selected_case_id, "client_request_id": request_id})
            st.session_state["problem_id"] = started["problem_id"]
            st.session_state["case_id"] = selected_case_id
            st.rerun()
        except Exception as exc:
            st.error(f"Could not start investigation: {exc}")

    problem_id = st.session_state.get("problem_id")
    if problem_id:
        st.markdown(f"#### Active investigation `{problem_id}`")

        @st.fragment(run_every=f"{REFRESH_SECONDS}s")
        def progress_view() -> None:
            _render_board(problem_id)

        progress_view()
    else:
        st.caption("Refreshing this page only reads status; a case starts only when you press the button above.")


if __name__ == "__main__":
    main()
