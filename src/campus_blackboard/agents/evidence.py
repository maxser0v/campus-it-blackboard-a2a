"""Evidence agent: read shared context, do one task, publish findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .common import (
    SpecialistRunError,
    entry_data,
    knowledge_type,
    open_evidence_requests,
    result_entry_id,
    write_request_id,
)
from .validation import (
    validate_tool_choice,
)

if TYPE_CHECKING:
    from .runner import SpecialistRunner


async def collect_evidence(
    agent: SpecialistRunner,
    problem_id: str,
    case_id: str,
    dispatch_id: str,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Collect one observation and publish it with its original tool provenance."""
    choice, tool_result = await _collect_tool_results(agent, case_id, summary, entries)
    if not isinstance(tool_result, dict) or not isinstance(
        tool_result.get("records"), list
    ):
        raise SpecialistRunError("fixture tool returned an invalid result object")
    request_id = choice["fulfilled_request_entry_id"]
    data = {
        "tool_name": tool_result["tool_name"],
        "query": tool_result["query"],
        "source_ids": tool_result["source_ids"],
        "observed_at": tool_result["observed_at"],
        "records": tool_result["records"],
        **(
            {"components": tool_result["components"]}
            if "components" in tool_result
            else {}
        ),
        "rationale": choice["rationale"],
        "fulfilled_request_entry_id": request_id,
        "fulfilled_request_status": (
            "fulfilled" if tool_result["records"] else "unavailable"
        )
        if request_id
        else None,
        "dispatch_result": {
            "dispatch_id": dispatch_id,
            "role": agent.role,
            "purpose": "collect_evidence",
            "case_id": case_id,
            "model_mode": getattr(agent.model, "mode", "unknown"),
            "model_usage": list(agent.last_run_usage),
        },
    }
    try:
        response = await agent.call_blackboard(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="observation",
            data=data,
            request_id=write_request_id(dispatch_id, "observation"),
        )
    except Exception:
        # Another delivery may have committed after our initial read, or
        # the write acknowledgement may have been lost after commit.
        snapshot = await agent.call_blackboard("read_blackboard", problem_id=problem_id)
        replay = completed_evidence(agent, snapshot, problem_id, case_id, dispatch_id)
        if replay is not None:
            return replay
        raise
    entry_id = result_entry_id(response, f"dispatch:{dispatch_id}:observation")
    return _evidence_result(agent, problem_id, dispatch_id, entry_id, data)


def completed_evidence(
    agent: SpecialistRunner,
    snapshot: Mapping[str, Any],
    problem_id: str,
    case_id: str,
    dispatch_id: str,
) -> dict[str, Any] | None:
    # History retains the committed original even if an observation was later revised.
    candidates = snapshot.get("history", []) or snapshot.get("entries", [])
    for entry in candidates:
        if (
            not isinstance(entry, Mapping)
            or knowledge_type(entry) != "observation"
            or entry.get("source_agent") != agent.actor
            or entry.get("problem_id") != problem_id
        ):
            continue
        data = entry_data(entry)
        result = data.get("dispatch_result")
        if not isinstance(result, Mapping) or any(
            result.get(key) != value
            for key, value in {
                "dispatch_id": dispatch_id,
                "role": agent.role,
                "purpose": "collect_evidence",
                "case_id": case_id,
            }.items()
        ):
            continue
        return _evidence_result(
            agent, problem_id, dispatch_id, str(entry["entry_id"]), data
        )
    return None


def _evidence_result(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    entry_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Use the saved usage and provenance for both new and repeated deliveries."""
    result = data["dispatch_result"]
    state = (
        "returned records" if data.get("records") else "returned no matching records"
    )
    return {
        "ok": True,
        "role": agent.role,
        "problem_id": problem_id,
        "purpose": "collect_evidence",
        "dispatch_id": dispatch_id,
        "entry_ids": [entry_id],
        "summary": f"{data['tool_name']} {state}; recorded tool provenance and request linkage.",
        "model_mode": result["model_mode"],
        "model_usage": result["model_usage"],
    }


async def _collect_tool_results(
    agent: SpecialistRunner,
    case_id: str,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a live baseline, fulfill an exact request, or ask the model for a query."""
    open_requests = open_evidence_requests(entries)
    context = {
        "problem": summary,
        "open_evidence_requests": open_requests,
        "available_tools": {
            "query_metrics": {
                "arguments": {
                    "metric_names": "list of metric names",
                    "contains": "optional string",
                }
            },
            "query_logs": {
                "arguments": {
                    "contains": "substring filter",
                    "service": "optional service filter",
                    "level": "optional severity",
                    "limit": "optional 1-100",
                }
            },
            "get_change_history": {
                "arguments": {
                    "service": "optional service filter",
                    "limit": "optional 1-100",
                }
            },
        },
        "_output_schema": {
            "tool": "allowlisted tool name",
            "query": "object of that tool's permitted arguments",
            "rationale": "string",
            "fulfilled_request_entry_id": "string or null",
        },
    }
    if (
        not open_requests
        and not any(knowledge_type(e) == "observation" for e in entries)
        and getattr(agent.model, "mode", "") == "live"
    ):
        # One bounded baseline, published atomically with provenance. No model
        # guesses are needed to discover which records exist.
        components = [
            agent.tools.execute(tool, case_id=case_id, arguments={})
            for tool in ("query_metrics", "query_logs", "get_change_history")
        ]
        choice = {
            "rationale": "Collect bounded baseline metrics, recent logs, and changes before diagnosis.",
            "fulfilled_request_entry_id": None,
        }
        tool_result = {
            "tool_name": "baseline",
            "query": {},
            "components": components,
            "records": [r for item in components for r in item["records"]],
            "source_ids": [r for item in components for r in item["source_ids"]],
            "observed_at": components[0]["observed_at"],
        }
        return choice, tool_result

    if open_requests and getattr(agent.model, "mode", "") == "live":
        # An approved exact query is execution work, not another model
        # decision. Preserve the request rather than asking an LLM to copy it.
        reviewer_ids = {
            str(e.get("entry_id")) for e in entries if e.get("source_agent") == "review"
        }
        selected = next(
            (r for r in open_requests if r["entry_id"] in reviewer_ids),
            open_requests[0],
        )
        query = dict(selected["query"])
        if isinstance(query.get("limit"), float) and query["limit"].is_integer():
            query["limit"] = int(query["limit"])
        choice = validate_tool_choice(
            {
                "tool": selected["requested_tool"],
                "query": query,
                "rationale": selected["reason"],
                "fulfilled_request_entry_id": selected["entry_id"],
            },
            open_requests,
        )
    else:
        choice = await agent.ask_model(
            "collect_evidence",
            context,
            lambda output: validate_tool_choice(output, open_requests),
        )
    tool_result = agent.tools.execute(
        choice["tool"], case_id=case_id, arguments=choice["query"]
    )
    return choice, tool_result


def build_handler(settings=None, **dependencies):
    """Expose this agent as an A2A task handler."""
    from .runner import build_specialist_handler

    return build_specialist_handler("evidence", settings, **dependencies)
