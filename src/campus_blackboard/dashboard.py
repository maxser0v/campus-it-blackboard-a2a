"""Live Streamlit view for an investigation; refreshes never start work."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components

from campus_blackboard.agent_theatre import theatre_html
from campus_blackboard import dashboard_ui as ui

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


# Kept as aliases for callers that use the dashboard's presentation helpers.
_data = ui.data
_evidence_label = ui.evidence_label
_evidence_request_status = ui.evidence_request_status


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
    entry_map = {str(entry.get("entry_id")): entry for entry in entries if entry.get("entry_id")}
    activity = sorted(history or entries, key=lambda entry: str(entry.get("created_at", "")))
    observations = [entry for entry in entries if entry.get("knowledge_type") == "observation"]
    hypotheses = [entry for entry in entries if entry.get("knowledge_type") == "hypothesis"]
    reviews = [entry for entry in entries if entry.get("knowledge_type") == "review"]
    solutions = [entry for entry in entries if entry.get("knowledge_type") == "solution"]
    requests = [entry for entry in entries if entry.get("knowledge_type") == "evidence_request"]
    decisions = [entry for entry in (history or entries) if entry.get("knowledge_type") == "controller_decision"]

    ui.overview(problem, status, entries, history or entries)
    # Reserve this position so a terminal notice never resets the selected tab.
    notice = st.empty()
    result = status.get("result") or {}
    if status.get("status") == "blocked":
        notice.error(f"Investigation blocked · {result.get('error') or result.get('reason', 'Operational failure')}")
    elif status.get("status") == "unresolved":
        notice.warning(f"More evidence is needed · {ui.human(result.get('reason', 'The current evidence does not support a completed response.'))}")
    elif status.get("status") == "completed":
        notice.success("Investigation complete · The diagnosis and response passed review. No remediation was executed.")

    tabs = st.tabs(["Observations", "Hypotheses", "Activity", "Controller decisions", "Response", "Agent theatre"])
    with tabs[0]:
        ui.observations_view(observations)
    with tabs[1]:
        ui.hypotheses_view(hypotheses, reviews, history, entry_map)
        ui.requests_view(requests, entries)
    with tabs[2]:
        ui.activity_view(activity, problem_id)
    with tabs[3]:
        ui.controller_view(decisions)
    with tabs[4]:
        ui.response_view(solutions, reviews, entry_map)
    with tabs[5]:
        theatre = theatre_html(board, status, problem_id)
        if hasattr(st, "iframe"):
            st.iframe(theatre, height=850, alt="Animated specialist workflow and event replay")
        else:
            components.html(theatre, height=850, scrolling=True)


def main() -> None:
    st.set_page_config(page_title="Campus Incident Investigator", layout="wide")
    ui.install_style()
    ui.html('<div class="incident-hero"><div><h1>Campus IT Incident Investigator</h1>'
            '<p>Follow the evidence, challenge the diagnosis, and review the proposed response.</p></div></div>')
    if settings.model_mode == "offline":
        ui.html('<div class="mode-bar"><span>' + ui.pill("Offline demo", "cyan")
                + ' &nbsp; Synthetic records · Fixture-driven agent responses</span><span>No model-provider calls</span></div>')
    else:
        model_label = (settings.codex_model or "Codex default") if settings.model_provider == "codex_cli" else (settings.model_name or "provider default")
        if settings.model_provider == "codex_cli" and settings.codex_reasoning_effort:
            model_label += f" · reasoning: {settings.codex_reasoning_effort}"
        ui.html('<div class="mode-bar"><span>' + ui.pill("Live model", "mint")
                + f' &nbsp; {ui.text(settings.model_provider)}</span><span>{ui.text(model_label)}</span></div>')

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
    with st.container(border=True):
        selection, action = st.columns([3, 1], vertical_alignment="bottom")
        with selection:
            selected_case_id = st.selectbox(
                "Investigation case",
                options=list(options),
                format_func=lambda case_id: options[case_id]["title"],
            )
        with action:
            start_clicked = st.button("Start a fresh investigation", type="primary", use_container_width=True)
        ui.html(f'<div class="case-symptom">{ui.text(options[selected_case_id].get("symptoms", ""))}</div>')
    if start_clicked:
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
        with st.expander("Investigation details"):
            st.caption(f"Investigation ID: {problem_id}")
            st.caption("Refresh reads the shared board only. New work starts when you press Start a fresh investigation.")

        @st.fragment(run_every=f"{REFRESH_SECONDS}s")
        def progress_view() -> None:
            _render_board(problem_id)

        progress_view()
    else:
        ui.empty("Ready to investigate", "Choose a case above and start an investigation. Follow the findings in each view or watch the specialists collaborate in Agent theatre.")
        ui.html('<div class="start-grid">' + ''.join(
            f'<div class="start-card" style="--accent:{color}"><h3>{name}</h3><p>{description}</p></div>'
            for index, name, description, color in [
                ("01", "Collect evidence", "Gather relevant measurements, logs, and recent changes.", "#63d9ee"),
                ("02", "Build a diagnosis", "Connect the observations and revise explanations.", "#b29aff"),
                ("03", "Challenge the findings", "Check the evidence and request missing information.", "#ffce83"),
                ("04", "Propose a response", "Recommend a bounded response with expected checks.", "#77e3ba"),
            ]) + '</div>')
        ui.html('<div class="footer-note">Synthetic campus incidents. Recommendations only; the prototype does not change campus systems.</div>')


if __name__ == "__main__":
    main()
