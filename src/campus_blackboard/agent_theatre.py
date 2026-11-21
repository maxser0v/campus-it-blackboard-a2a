"""Read-only, event-driven presentation of the specialist workflow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROLES = {"evidence", "diagnosis", "review", "response"}


def theatre_events(board: dict[str, Any]) -> list[dict[str, str]]:
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
        events.append({
            "role": role, "phase": phase, "title": title, "detail": detail[:900],
            "time": str(entry.get("created_at", "")),
            "dispatch": str(data.get("dispatch_id", "")),
            "reference": str(entry.get("entry_id", "")),
        })
    return events


def theatre_html(board: dict[str, Any], status: dict[str, Any], problem_id: str) -> str:
    payload = json.dumps({"events": theatre_events(board), "status": status.get("status", "unknown"), "id": problem_id}, ensure_ascii=True)
    # JSON lives inside a script element; escape HTML delimiters even in recorded text.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Path(__file__).with_name("agent_theatre.html").read_text().replace("__THEATRE_DATA__", payload)
