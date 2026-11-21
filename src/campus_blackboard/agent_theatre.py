"""Read-only, event-driven presentation of the specialist workflow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROLES = {"evidence", "diagnosis", "review", "response"}


def _explanation_sections(kind: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose recorded explanations and evidence through an explicit field allowlist."""
    fields = {
        "controller_decision": [("purpose", "Assigned task"), ("trigger", "Why this task was assigned"),
                                ("summary", "Completion summary"), ("result_entry_ids", "Published entries"),
                                ("error", "Failure details"), ("reason", "Stopping reason")],
        "observation": [("rationale", "Why this evidence was collected"), ("tool_name", "Tool used"),
                        ("query", "Tool arguments"), ("records", "Observed results"),
                        ("source_ids", "Sources"), ("fulfilled_request_entry_id", "Request answered"),
                        ("fulfilled_request_status", "Request outcome")],
        "hypothesis": [("claim", "Published hypothesis"), ("status", "Assessment"),
                       ("supporting_evidence_ids", "Supporting evidence"),
                       ("contradicting_evidence_ids", "Contradicting evidence"),
                       ("observation_refs", "Observations considered"), ("open_questions", "Open questions")],
        "evidence_request": [("question", "Question to resolve"), ("reason", "Why more evidence is needed"),
                             ("requested_tool", "Requested tool"), ("query", "Requested arguments"),
                             ("status", "Request status")],
        "review": [("verdict", "Review verdict"), ("rationale", "Published review explanation"),
                   ("target_entry_id", "Reviewed entry"), ("target_revision", "Reviewed revision"),
                   ("supporting_evidence_ids", "Evidence checked"), ("required_follow_up", "Required follow-up")],
        "solution": [("summary", "Proposed response"), ("actions", "Recovery steps and expected checks"),
                     ("evidence_ids", "Evidence supporting the proposal"), ("hypothesis_refs", "Diagnosis references"),
                     ("runbook_ids", "Runbook references"), ("caveats", "Limitations and caveats")],
    }
    sections = []
    for key, label in fields.get(kind, []):
        value = data.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if key == "actions" and isinstance(value, list):
            text = "\n\n".join(
                f"{i}. {action.get('step', 'Action')}\nPurpose: {action.get('purpose', '')}"
                f"\nExpected check: {action.get('expected_check', '')}"
                for i, action in enumerate(value, 1) if isinstance(action, dict)
            )
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            text = str(value).replace("_", " ") if key in {"purpose", "status", "verdict", "fulfilled_request_status", "reason"} else str(value)
        section: dict[str, Any] = {"label": label, "text": text}
        if key == "records" and isinstance(value, list):
            section["records"] = value
        sections.append(section)
    return sections


def theatre_events(board: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the append-only history into public workflow events, not model thoughts."""
    events = []
    history = board.get("history") or board.get("entries", [])
    for entry in sorted(history, key=lambda item: str(item.get("created_at", ""))):
        data = entry.get("data", {})
        if not isinstance(data, dict):
            continue
        kind = entry.get("knowledge_type", "")
        event = data.get("event_type", "")
        role = str(data.get("agent") if kind == "controller_decision" else entry.get("source_agent", ""))
        if kind == "controller_decision":
            if event == "terminal_decision":
                role = "controller"
                phase = "terminal"
                title = str(data.get("status", "finished")).replace("_", " ").title()
                detail = str(data.get("reason", "")).replace("_", " ")
            elif event in {"dispatch_started", "dispatch_finished"} and role in ROLES:
                phase = "started" if event == "dispatch_started" else "failed" if data.get("status") == "failed" else "finished"
                title = {"started": "Task assigned", "finished": "Task completed", "failed": "Task failed"}[phase]
                detail = str(data.get("error") or data.get("summary") or data.get("purpose", "")).replace("_", " ")
            else:
                continue
        elif role in ROLES:
            phase = "published"
            title = str(kind).replace("_", " ").title() + " shared"
            detail = str(data.get("claim") or data.get("question") or data.get("summary") or data.get("rationale") or data.get("tool_name") or data.get("verdict") or kind)
        else:
            continue
        dispatch_result = data.get("dispatch_result")
        dispatch_result = dispatch_result if isinstance(dispatch_result, dict) else {}
        events.append({
            "role": role, "phase": phase, "title": title, "detail": detail[:900],
            "time": str(entry.get("created_at", "")),
            "dispatch": str(data.get("dispatch_id") or dispatch_result.get("dispatch_id", "")),
            "reference": str(entry.get("entry_id", "")),
            "revision": entry.get("revision"),
            "sections": _explanation_sections(kind, data),
        })
    return events


def theatre_html(board: dict[str, Any], status: dict[str, Any], problem_id: str) -> str:
    payload = json.dumps({"events": theatre_events(board), "status": status.get("status", "unknown"), "id": problem_id}, ensure_ascii=True)
    # JSON lives inside a script element; escape HTML delimiters even in recorded text.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Path(__file__).with_name("agent_theatre.html").read_text().replace("__THEATRE_DATA__", payload)
