"""Presentation components for the read-only investigation workspace."""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st


COLORS = {"evidence": "#63d9ee", "diagnosis": "#b29aff", "review": "#ffce83", "response": "#77e3ba", "controller": "#96bfff"}
TONES = {"evidence": "cyan", "diagnosis": "violet", "review": "amber", "response": "mint", "controller": "muted"}
TOOL_NAMES = {"query_metrics": "Service metrics", "query_logs": "Service logs", "get_change_history": "Recent changes", "get_runbook": "Runbook guidance"}
RULE_LABELS = {
    "no_observations": "Collect initial observations before forming a diagnosis.",
    "no_hypotheses": "The available evidence needs an initial diagnosis.",
    "open_evidence_request": "A specialist requested a targeted evidence check.",
    "unreviewed_hypothesis_revision": "The current hypothesis revision needs an independent review.",
    "review_rejected_hypothesis": "Revise the diagnosis in response to the reviewer's objections.",
    "new_observation_not_considered": "Reconsider the diagnosis with the newly collected evidence.",
    "observation_revision_changed": "Reconsider the diagnosis because its underlying evidence changed.",
    "unreviewed_solution_revision": "Check the current response proposal against the accepted diagnosis.",
    "response_rejected": "Revise the response proposal to address review feedback.",
}


def text(value: Any) -> str:
    return escape(str(value), quote=True)


def human(value: Any) -> str:
    return str(value).replace("_", " ")


def data(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("data", {})
    return value if isinstance(value, dict) else {}


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def install_style() -> None:
    html("<style>" + Path(__file__).with_name("dashboard.css").read_text() + "</style>")


def pill(label: Any, tone: str = "muted") -> str:
    safe_tone = tone if tone in {"mint", "amber", "rose", "violet", "cyan", "muted"} else "muted"
    return f'<span class="pill {safe_tone}">{text(label)}</span>'


def status_tone(value: str) -> str:
    if value in {"completed", "accept", "supported", "fulfilled"}:
        return "mint"
    if value in {"blocked", "failed", "reject", "rejected"}:
        return "rose"
    if value in {"unresolved", "unavailable", "request_evidence", "open", "proposed"}:
        return "amber"
    return "muted"


def timestamp(value: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        suffix = moment.strftime(" %Z") if moment.tzinfo else ""
        return moment.strftime("%d %b · %H:%M:%S") + suffix
    except (ValueError, TypeError):
        return str(value or "Time not recorded")


def section(title: str, subtitle: str, count: int | None = None) -> None:
    html(f'<div class="section-heading"><div><h3>{text(title)}</h3><p>{text(subtitle)}</p></div>'
         + (pill(f"{count} recorded") if count is not None else "") + '</div>')


def empty(title: str, description: str) -> None:
    html(f'<div class="empty-state"><h2>{text(title)}</h2><p>{text(description)}</p></div>')


def latest_review(entry: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    matched = [review for review in reviews if data(review).get("target_entry_id") == entry.get("entry_id")
               and data(review).get("target_revision") == entry.get("revision")]
    return sorted(matched, key=lambda review: str(review.get("created_at", "")))[-1] if matched else None


def evidence_request_status(request: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    request_id = str(request.get("entry_id", ""))
    statuses = {str(data(entry).get("fulfilled_request_status", "")).lower() for entry in entries
                if entry.get("knowledge_type") == "observation" and str(data(entry).get("fulfilled_request_entry_id", "")) == request_id}
    if "fulfilled" in statuses:
        return "fulfilled"
    if "unavailable" in statuses:
        return "unavailable"
    return str(data(request).get("status", "open"))


def evidence_label(entry_id: str, entries: dict[str, dict[str, Any]]) -> str:
    entry = entries.get(entry_id)
    if not entry:
        return f"{entry_id} (not found on this board)"
    values = data(entry)
    records = values.get("records", [])
    summary = ", ".join(f"{human(row.get('name', 'value'))}: {row.get('value', '')} {row.get('unit', '')}" for row in records[:3] if isinstance(row, dict))
    return f"{entry_id} · {values.get('tool_name', 'observation')}" + (f" · {summary}" if summary else "")


def citations(refs: list[Any], entries: dict[str, dict[str, Any]], *, empty_label: str = "None cited for this revision.") -> None:
    if not refs:
        st.caption(empty_label)
    for ref in refs:
        entry = entries.get(str(ref))
        if entry:
            values = data(entry)
            lines = [f"{human(row.get('name', 'value'))}: {row.get('value', '')} {row.get('unit', '')}" for row in values.get("records", [])[:3] if isinstance(row, dict)]
            title = TOOL_NAMES.get(values.get("tool_name"), human(values.get("tool_name", "Observation")))
            description = " · ".join(lines) or str(values.get("rationale", "Recorded observation"))
        else:
            title, description = "Observation unavailable", "This reference was not found on the current board."
        html(f'<div class="reference-card"><strong>{text(title)}</strong><p>{text(description)}</p><code>{text(ref)}</code></div>')


def overview(problem: dict[str, Any], status: dict[str, Any], entries: list[dict[str, Any]], history: list[dict[str, Any]]) -> None:
    run_status = str(status.get("status", "unknown"))
    observations = sum(entry.get("knowledge_type") == "observation" for entry in entries)
    hypotheses = sum(entry.get("knowledge_type") == "hypothesis" for entry in entries)
    started = {data(entry).get("dispatch_id") or entry.get("entry_id") for entry in history if data(entry).get("event_type") == "dispatch_started"}
    dispatches = (status.get("result") or {}).get("dispatch_count", len(started))
    html(f'<div class="workspace-heading"><div>'
         f'<h2>{text(problem.get("title", "Active investigation"))}</h2></div><small>Shared board · revision {text(problem.get("board_revision", "—"))}<br>Updates every 3 seconds</small></div>')
    facts = [("Observations", observations), ("Hypotheses", hypotheses), ("Specialist tasks", dispatches)]
    html('<div class="run-summary">' + pill(human(run_status).title(), status_tone(run_status))
         + ''.join(f'<span class="run-fact"><strong>{text(value)}</strong> {text(label)}</span>' for label, value in facts) + '</div>')


def record_table(records: list[Any]) -> str:
    rows = [row for row in records if isinstance(row, dict)]
    if not rows:
        return '<div class="record-copy">This query returned no records.</div>'
    fields = [key for key in ("name", "value", "unit", "message", "event", "scope", "component", "status", "timestamp") if any(key in row for row in rows)][:5]
    if not fields:
        return '<div class="record-copy">Source records are available in the collection details below.</div>'
    labels = {"name": "Measurement", "value": "Value", "unit": "Unit", "scope": "Service"}
    head = ''.join(f'<th scope="col">{text(labels.get(key, human(key).title()))}</th>' for key in fields)
    body = ''.join('<tr>' + ''.join(f'<td class="{"numeric" if key == "value" else ""}">{text(human(row.get(key, "—")) if key == "name" else row.get(key, "—"))}</td>' for key in fields) + '</tr>' for row in rows)
    return f'<div class="table-wrap" tabindex="0" role="region" aria-label="Evidence source records"><table class="evidence-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div><div class="table-hint">Scroll the table to see all columns.</div>'


def observations_view(observations: list[dict[str, Any]]) -> None:
    section("Evidence collected", "Measurements, logs, and changes gathered by the Evidence Agent.", len(observations))
    if not observations:
        empty("Waiting for the first observation", "The Evidence Agent will gather relevant records and publish them here. Follow the team in Agent theatre while it works.")
    for observation in observations:
        values = data(observation)
        title = TOOL_NAMES.get(values.get("tool_name"), human(values.get("tool_name", "Observation")).title())
        count = len(values.get("records", []))
        html(f'<article class="record-card" style="--accent:{COLORS["evidence"]}"><div class="record-head">{pill("Evidence agent", "cyan")}<span class="record-meta">{text(timestamp(values.get("observed_at", observation.get("created_at"))))}</span></div>'
             f'<h4 class="record-title">{text(title)}</h4><p class="record-copy">{text(values.get("rationale", "Recorded evidence from the current investigation."))}</p>'
             + record_table(values.get("records", []))
             + f'<div class="record-footer"><span>{count} source records</span><span>Revision {text(observation.get("revision", "—"))}</span></div></article>')
        with st.expander(f"Collection details · {observation.get('entry_id', '')}"):
            st.caption(f"Published by {observation.get('source_agent', 'evidence')} · {observation.get('created_at', '')}")
            st.json({"query": values.get("query", {}), "source_ids": values.get("source_ids", []), "records": values.get("records", [])}, expanded=False)


def hypotheses_view(hypotheses: list[dict[str, Any]], reviews: list[dict[str, Any]], history: list[dict[str, Any]], entries: dict[str, dict[str, Any]]) -> None:
    section("How the diagnosis is evolving", "Compare explanations, inspect the evidence, and follow each revision.", len(hypotheses))
    if not hypotheses:
        empty("The diagnosis is taking shape", "Hypotheses appear after the initial evidence is collected. Each revision is checked against the current observations.")
    for hypothesis in sorted(hypotheses, key=lambda entry: ({"supported": 0, "proposed": 1, "unresolved": 2, "weakened": 3, "rejected": 4}.get(str(data(entry).get("status")), 5), str(entry.get("entry_id")))):
        values = data(hypothesis)
        assessment = str(values.get("status", "unknown"))
        review = latest_review(hypothesis, reviews)
        verdict = str(data(review).get("verdict", "not reviewed")) if review else "not reviewed"
        with st.container(border=True):
            html(f'<div class="record-head">{pill("Diagnosis agent", "violet")}<span>{pill(human(assessment).title(), status_tone(assessment))}</span></div>'
                 f'<h4 class="record-title">{text(values.get("claim", "Hypothesis"))}</h4>'
                 f'<div class="record-footer"><span>Revision {text(hypothesis.get("revision", "—"))}</span><span>Review: {text(human(verdict))}</span></div>')
            if review:
                html(f'<div class="review-note"><strong>REVIEW OF THIS REVISION · {text(human(verdict).upper())}</strong><br>{text(data(review).get("rationale", "No explanation recorded."))}</div>')
            supports, contradicts = st.columns(2)
            with supports:
                st.markdown("**Supporting evidence**")
                citations(values.get("supporting_evidence_ids", []), entries)
            with contradicts:
                st.markdown("**Contradicting evidence**")
                citations(values.get("contradicting_evidence_ids", []), entries)
            if values.get("open_questions"):
                html('<div class="questions"><strong>Still to resolve</strong><br>' + '<br>'.join(text(question) for question in values["open_questions"]) + '</div>')
            revisions = sorted([item for item in history if item.get("knowledge_type") == "hypothesis" and item.get("entry_id") == hypothesis.get("entry_id")], key=lambda item: int(item.get("revision", 1)))
            with st.expander(f"Revision history & references · {len(revisions) or 1} versions"):
                st.caption(f"Hypothesis ID: {hypothesis.get('entry_id')}")
                for revision in revisions or [hypothesis]:
                    revision_data = data(revision)
                    html(f'<div class="reference-card"><strong>Revision {text(revision.get("revision"))} · {text(human(revision_data.get("status", "unknown")))}</strong><p>{text(revision_data.get("claim", ""))}</p></div>')
                    st.json({key: revision_data.get(key, []) for key in ("supporting_evidence_ids", "contradicting_evidence_ids", "observation_refs", "open_questions")}, expanded=False)


def requests_view(requests: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    if not requests:
        return
    section("Questions sent back to Evidence", "Targeted checks requested by Diagnosis or Review.", len(requests))
    for request in requests:
        values = data(request)
        outcome = evidence_request_status(request, entries)
        html(f'<div class="record-card" style="--accent:#ffce83"><div class="record-head">{pill(human(outcome).title(), status_tone(outcome))}<span class="record-meta">Requested by {text(human(request.get("source_agent", "specialist")))}</span></div>'
             f'<h4 class="record-title">{text(values.get("question", "Evidence request"))}</h4><p class="record-copy">{text(values.get("reason", ""))}</p></div>')
        with st.expander(f"Requested check · {request.get('entry_id', '')}"):
            st.json({"tool": values.get("requested_tool"), "query": values.get("query", {}), "status": outcome}, expanded=False)


def activity_view(activity: list[dict[str, Any]], problem_id: str) -> None:
    section("The investigation, as it happened", "A shared timeline of specialist findings and controller decisions.", len(activity))
    selected = st.selectbox("Show activity from", ["All agents", "Controller", "Evidence", "Diagnosis", "Review", "Response"], key=f"activity-filter-{problem_id}")
    visible = [item for item in activity if selected == "All agents" or item.get("source_agent") == selected.lower()]
    if not visible:
        empty("No activity here yet", "Recorded tasks and findings will appear as this agent contributes to the investigation.")
    for item in reversed(visible):
        values = data(item)
        role = str(item.get("source_agent", "unknown"))
        kind = human(item.get("knowledge_type", "entry")).title()
        title = values.get("summary") or values.get("claim") or values.get("question") or human(values.get("event_type", kind)).title()
        detail = values.get("rationale") or values.get("reason") or values.get("purpose", "")
        outcome = str(values.get("status") or values.get("verdict") or "")
        html(f'<div class="timeline-item"><div class="timeline-time">{text(timestamp(item.get("created_at")))}</div><article class="timeline-body" style="--accent:{COLORS.get(role, "#96bfff")}">'
             f'{pill(human(role).title(), TONES.get(role, "muted"))} <span class="event-kind">{text(kind)}</span> '
             + (pill(human(outcome), status_tone(outcome)) if outcome else "")
             + f'<h4 class="record-title">{text(title)}</h4><p class="record-copy">{text(human(detail))}</p>'
             f'<div class="timeline-meta">Revision {text(item.get("revision", "—"))} · {text(item.get("entry_id", ""))}</div></article></div>')


def controller_view(decisions: list[dict[str, Any]]) -> None:
    section("Why the controller chose each task", "Dispatch triggers, task outcomes, and the decision to stop.")
    if not decisions:
        empty("The controller is preparing the first task", "Dispatch decisions will appear here once a specialist is assigned.")
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(decisions):
        values = data(entry)
        key = str(values.get("dispatch_id") or entry.get("entry_id") or index)
        groups.setdefault(key, []).append(entry)
    for group in sorted(groups.values(), key=lambda items: max(str(item.get("created_at", "")) for item in items), reverse=True):
        ordered = sorted(group, key=lambda item: str(item.get("created_at", "")))
        first, last = data(ordered[0]), data(ordered[-1])
        terminal = last.get("event_type") == "terminal_decision"
        role = str(first.get("agent", "controller"))
        outcome = str(last.get("status", "working"))
        title = "Investigation decision" if terminal else human(first.get("purpose", "Specialist task")).title()
        trigger = first.get("trigger", {})
        rule = trigger.get("rule", "") if isinstance(trigger, dict) else ""
        explanation = human(last.get("reason", "")) if terminal else RULE_LABELS.get(str(rule), human(rule) or "Task assigned from the current Blackboard state.")
        html(f'<div class="record-card" style="--accent:{COLORS.get(role, "#96bfff")}"><div class="record-head">{pill(role.title(), TONES.get(role, "muted"))}{pill(human(outcome).title(), status_tone(outcome))}</div>'
             f'<h4 class="record-title">{text(title)}</h4><p class="record-copy">{text(explanation)}</p>'
             + (f'<p class="record-copy">{text(last["summary"])}</p>' if last.get("summary") else "")
             + f'<div class="record-footer"><span>{text(timestamp(ordered[-1].get("created_at")))}</span><span>{len(last.get("result_entry_ids", []))} published entries</span></div></div>')
        if last.get("error"):
            st.error(str(last["error"]))
        with st.expander(f"Decision details · {role.title()} · {title}"):
            if last.get("task_id"):
                st.caption(f"A2A task ID: {last['task_id']}")
            st.json([{"entry_id": item.get("entry_id"), "created_at": item.get("created_at"), **data(item)} for item in ordered], expanded=False)


def response_view(solutions: list[dict[str, Any]], reviews: list[dict[str, Any]], entries: dict[str, dict[str, Any]]) -> None:
    section("A response grounded in the evidence", "A proposed course of action, checked against the diagnosis and supporting records.")
    if not solutions:
        empty("The response comes after review", "Once a supported diagnosis passes review, the Response Agent will propose recovery steps and the checks needed to validate them.")
        return
    solution = max(solutions, key=lambda item: (str(item.get("created_at", "")), int(item.get("revision", 1))))
    values = data(solution)
    review = latest_review(solution, reviews)
    verdict = str(data(review).get("verdict", "not reviewed")) if review else "not reviewed"
    html(f'<div class="record-card" style="--accent:#77e3ba"><div class="record-head">{pill("Response agent", "mint")}{pill(human(verdict).title(), status_tone(verdict))}</div>'
         f'<h4 class="record-title">{text(values.get("summary", "Response proposal"))}</h4><div class="record-footer"><span>Proposal · revision {text(solution.get("revision", "—"))}</span><span>Recommendation only</span></div></div>')
    if review:
        html(f'<div class="review-note"><strong>REVIEW OF THIS PROPOSAL</strong><br>{text(data(review).get("rationale", "No explanation recorded."))}</div>')
    for index, action in enumerate(values.get("actions", []), 1):
        if not isinstance(action, dict):
            continue
        html(f'<div class="response-action"><span class="action-number">{index:02}</span><div><h4>{text(action.get("step", "Action"))}</h4><p>{text(action.get("purpose", ""))}</p>'
             + (f'<div class="expected-check"><strong>Expected check</strong><br>{text(action["expected_check"])}</div>' if action.get("expected_check") else "") + '</div></div>')
    evidence, guidance = st.columns([1.4, 1])
    with evidence:
        st.markdown("**Evidence behind this proposal**")
        citations(values.get("evidence_ids", []), entries)
    with guidance:
        st.markdown("**Runbook guidance**")
        for ref in values.get("runbook_ids", []):
            html(f'<div class="reference-card"><strong>{text(ref)}</strong></div>')
        for caveat in values.get("caveats", []):
            html(f'<div class="questions">{text(caveat)}</div>')
    with st.expander("Proposal references & review record"):
        st.json({"entry_id": solution.get("entry_id"), "hypothesis_refs": values.get("hypothesis_refs", []), "evidence_ids": values.get("evidence_ids", []), "runbook_ids": values.get("runbook_ids", []), "review": data(review) if review else None}, expanded=False)
    html('<div class="footer-note">No remediation was executed. Changes to campus systems require approval from the responsible service owner.</div>')
