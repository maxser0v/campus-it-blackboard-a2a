"""Small helpers for reading Blackboard entries and preventing duplicate work."""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from ..tools.queries import already_observed, signature

ROLES = frozenset({"evidence", "diagnosis", "review", "response"})
PURPOSES = {
    "evidence": frozenset({"collect_evidence"}),
    "diagnosis": frozenset({"diagnose"}),
    "review": frozenset({"review_hypotheses", "review_solution"}),
    "response": frozenset({"propose_response"}),
}
FIXTURE_TOOL_NAMES = frozenset({"query_metrics", "query_logs", "get_change_history"})
HYPOTHESIS_STATES = {"proposed", "supported", "weakened", "rejected", "unresolved"}
REVIEW_VERDICTS = {"accept", "reject", "request_evidence"}


class SpecialistRunError(RuntimeError):
    """An agent could not complete its task safely."""


def blackboard_entries(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = snapshot.get("entries", [])
    if not isinstance(value, list):
        raise SpecialistRunError("Blackboard snapshot has no entries array")
    return [dict(entry) for entry in value if isinstance(entry, Mapping)]


def knowledge_type(entry: Mapping[str, Any]) -> str:
    return str(entry.get("knowledge_type", ""))


def entry_data(entry: Mapping[str, Any]) -> dict[str, Any]:
    value = entry.get("data", {})
    return dict(value) if isinstance(value, Mapping) else {}


def entry_reference(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": str(entry.get("entry_id", "")),
        "revision": int(entry.get("revision", 1)),
    }


def write_request_id(dispatch_id: str, suffix: str) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"campus-blackboard:{dispatch_id}:{suffix}")
    )


def result_entry_id(result: Mapping[str, Any], fallback: str) -> str:
    for key in ("entry_id", "id"):
        if isinstance(result.get(key), str):
            return str(result[key])
    entry = result.get("entry")
    if isinstance(entry, Mapping) and isinstance(entry.get("entry_id"), str):
        return str(entry["entry_id"])
    entries = result.get("entries")
    if isinstance(entries, list) and entries and isinstance(entries[0], Mapping):
        if isinstance(entries[0].get("entry_id"), str):
            return str(entries[0]["entry_id"])
    return fallback


def is_error(result: Mapping[str, Any]) -> bool:
    return bool(result.get("error")) or result.get("ok") is False


def open_evidence_requests(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = {}
    for entry in entries:
        if knowledge_type(entry) == "observation":
            data = entry_data(entry)
            req_id = data.get("fulfilled_request_entry_id")
            if req_id:
                completed[str(req_id)] = data.get("fulfilled_request_status")
    result = []
    for entry in entries:
        if knowledge_type(entry) != "evidence_request":
            continue
        data = entry_data(entry)
        if str(data.get("status", "open")).lower() not in {"open", "pending"}:
            continue
        if already_observed(data.get("requested_tool"), data.get("query"), entries):
            continue
        if str(entry.get("entry_id")) in completed:
            continue
        result.append({"entry_id": str(entry.get("entry_id")), **data})
    return result


def query_signature(tool: Any, query: Any) -> tuple[str, str] | None:
    """Return a stable signature for one fixture tool query."""
    if (
        not isinstance(tool, str)
        or tool not in FIXTURE_TOOL_NAMES
        or not isinstance(query, Mapping)
    ):
        return None
    return signature(tool, dict(query))


def unavailable_queries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find exact tool queries already proven unavailable on this fixture case."""
    requests = {
        str(entry.get("entry_id")): entry
        for entry in entries
        if knowledge_type(entry) == "evidence_request"
    }
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if knowledge_type(entry) != "observation":
            continue
        data = entry_data(entry)
        if str(data.get("fulfilled_request_status", "")).lower() != "unavailable":
            continue
        request = requests.get(str(data.get("fulfilled_request_entry_id")))
        if request is not None:
            request_data = entry_data(request)
            tool, query = request_data.get("requested_tool"), request_data.get("query")
        else:
            tool, query = data.get("tool_name"), data.get("query")
        signature = query_signature(tool, query)
        if signature is not None:
            found[signature] = {"tool": signature[0], "query": dict(query)}
    return list(found.values())


def was_unavailable(tool: Any, query: Any, unavailable: list[dict[str, Any]]) -> bool:
    signature = query_signature(tool, query)
    return signature is not None and any(
        signature == query_signature(item.get("tool"), item.get("query"))
        for item in unavailable
    )


def reviews_by_revision(
    entries: list[dict[str, Any]],
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    result: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in entries:
        if knowledge_type(entry) != "review":
            continue
        data = entry_data(entry)
        target_id, target_revision = (
            data.get("target_entry_id"),
            data.get("target_revision"),
        )
        if target_id is not None and target_revision is not None:
            try:
                result.setdefault((str(target_id), int(target_revision)), []).append(
                    entry
                )
            except (ValueError, TypeError):
                continue
    return result


def same_request(request: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    data = entry_data(entry)
    return (
        request.get("requested_tool") == data.get("requested_tool")
        and request.get("query") == data.get("query")
        and request.get("question") == data.get("question")
    )
