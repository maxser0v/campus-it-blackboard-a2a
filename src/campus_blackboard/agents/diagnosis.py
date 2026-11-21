"""Diagnosis agent: read shared context, do one task, publish findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..tools.queries import already_observed
from .common import (
    FIXTURE_TOOL_NAMES,
    HYPOTHESIS_STATES,
    SpecialistRunError,
    entry_data,
    entry_reference,
    knowledge_type,
    open_evidence_requests,
    result_entry_id,
    same_request,
    unavailable_queries,
    was_unavailable,
    write_request_id,
)
from .validation import (
    validate_diagnosis,
)

if TYPE_CHECKING:
    from .runner import SpecialistRunner


async def diagnose(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> tuple[list[str], str]:
    """Compare explanations, publish hypotheses, and request missing evidence."""
    observations = [
        entry for entry in entries if knowledge_type(entry) == "observation"
    ]
    own_hypotheses = [
        entry
        for entry in entries
        if knowledge_type(entry) == "hypothesis"
        and entry.get("source_agent") == agent.actor
    ]
    context = {
        "problem": summary,
        "observations": observations,
        "own_hypotheses": own_hypotheses,
        "open_evidence_requests": open_evidence_requests(entries),
        "unavailable_evidence_queries": unavailable_queries(entries),
        "allowed_tools": [
            "query_metrics(metric_names, contains)",
            "query_logs(contains, service, level, limit)",
            "get_change_history(service, limit)",
        ],
        "_output_schema": {
            "hypotheses": [
                {
                    "claim": "string",
                    "status": sorted(HYPOTHESIS_STATES),
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "open_questions": [],
                    "revise_entry_id": "optional current own hypothesis entry ID",
                }
            ],
            "evidence_requests": [
                {
                    "question": "string",
                    "requested_tool": sorted(FIXTURE_TOOL_NAMES),
                    "query": "object",
                    "reason": "string",
                    "status": "open",
                }
            ],
        },
    }
    decision = await agent.ask_model(
        "diagnose",
        context,
        lambda output: validate_diagnosis(output, entries, agent.actor),
    )
    # Publish hypotheses first, then request only evidence we have not tried.
    published_ids = []
    for index, hypothesis in enumerate(decision["hypotheses"]):
        entry_id = await _publish_hypothesis(
            agent,
            problem_id,
            dispatch_id,
            index,
            hypothesis,
            own_hypotheses,
            observations,
        )
        published_ids.append(entry_id)

    request_ids, suppressed_unavailable_request = await _publish_evidence_requests(
        agent,
        problem_id,
        dispatch_id,
        decision["evidence_requests"],
        entries,
        context["unavailable_evidence_queries"],
    )
    published_ids.extend(request_ids)

    if not published_ids and suppressed_unavailable_request:
        entry_id = await _record_unresolved_finding(
            agent, problem_id, dispatch_id, own_hypotheses, observations
        )
        published_ids.append(entry_id)
    if not published_ids:
        raise SpecialistRunError(
            "diagnosis produced no hypotheses or new evidence requests"
        )
    return (
        published_ids,
        f"Processed {len(decision['hypotheses'])} hypothesis result(s); {len(decision['evidence_requests'])} follow-up request(s) were proposed before unavailable-query filtering.",
    )


async def _publish_hypothesis(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    index: int,
    hypothesis: dict[str, Any],
    own_hypotheses: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> str:
    """Create a hypothesis, or revise its existing entry only when it changed."""
    data = {
        key: hypothesis[key]
        for key in (
            "claim",
            "status",
            "supporting_evidence_ids",
            "contradicting_evidence_ids",
            "open_questions",
        )
    }
    data["observation_refs"] = [
        entry_reference(entry)
        for entry in sorted(observations, key=lambda item: str(item.get("entry_id")))
    ]
    revise_id = hypothesis.get("revise_entry_id")
    if revise_id:
        prior = next(
            entry for entry in own_hypotheses if str(entry.get("entry_id")) == revise_id
        )
        if entry_data(prior) == data:
            # Replaying unchanged analysis must not manufacture a new
            # revision that invalidates an otherwise current review.
            entry_id = revise_id
        else:
            response = await agent.call_blackboard(
                "update_knowledge",
                problem_id=problem_id,
                entry_id=revise_id,
                expected_revision=int(prior.get("revision", 1)),
                knowledge_type="hypothesis",
                data=data,
                request_id=write_request_id(
                    dispatch_id, f"hypothesis:{index}:{revise_id}"
                ),
            )
            entry_id = result_entry_id(response, revise_id)
    else:
        response = await agent.call_blackboard(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="hypothesis",
            data=data,
            request_id=write_request_id(dispatch_id, f"hypothesis:{index}"),
        )
        entry_id = result_entry_id(
            response, f"dispatch:{dispatch_id}:hypothesis:{index}"
        )
    return entry_id


async def _publish_evidence_requests(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    requests: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    unavailable: list[dict[str, Any]],
) -> tuple[list[str], bool]:
    """Reuse open requests and skip queries that already returned evidence or nothing."""
    published_ids = []
    suppressed_unavailable_request = False
    for index, request in enumerate(requests):
        if already_observed(
            request.get("requested_tool"), request.get("query"), entries
        ):
            continue
        if was_unavailable(
            request.get("requested_tool"), request.get("query"), unavailable
        ):
            suppressed_unavailable_request = True
            continue
        existing_request = next(
            (
                entry
                for entry in entries
                if same_request(request, entry)
                and knowledge_type(entry) == "evidence_request"
                and str(entry_data(entry).get("status", "open")).lower()
                in {"open", "pending"}
            ),
            None,
        )
        if existing_request is not None:
            published_ids.append(str(existing_request.get("entry_id")))
            continue
        response = await agent.call_blackboard(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="evidence_request",
            data=request,
            request_id=write_request_id(dispatch_id, f"evidence_request:{index}"),
        )
        published_ids.append(
            result_entry_id(
                response, f"dispatch:{dispatch_id}:evidence_request:{index}"
            )
        )
    return published_ids, suppressed_unavailable_request


async def _record_unresolved_finding(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    own_hypotheses: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> str:
    """Record an honest stopping point when all follow-ups are unavailable."""
    # All proposed follow-ups were already attempted without records.
    # Keep an honest unresolved finding on the board so the controller
    # can converge instead of treating the repeated request as a crash.
    prior = next(
        (
            entry
            for entry in own_hypotheses
            if entry_data(entry).get("status") == "unresolved"
        ),
        None,
    )
    if prior is not None:
        refreshed = {
            **entry_data(prior),
            "observation_refs": [
                entry_reference(entry)
                for entry in sorted(
                    observations, key=lambda item: str(item.get("entry_id"))
                )
            ],
        }
        if refreshed != entry_data(prior):
            await agent.call_blackboard(
                "update_knowledge",
                problem_id=problem_id,
                entry_id=str(prior["entry_id"]),
                expected_revision=int(prior.get("revision", 1)),
                data=refreshed,
                request_id=write_request_id(
                    dispatch_id, "refresh_unresolved_observation_refs"
                ),
            )
        return str(prior.get("entry_id"))
    else:
        unresolved = {
            "observation_refs": [
                entry_reference(entry)
                for entry in sorted(
                    observations, key=lambda item: str(item.get("entry_id"))
                )
            ],
            "claim": "The available fixture evidence remains insufficient to identify a root cause",
            "status": "unresolved",
            "supporting_evidence_ids": [
                str(entry.get("entry_id"))
                for entry in observations
                if entry_data(entry).get("records")
            ],
            "contradicting_evidence_ids": [],
            "open_questions": [],
        }
        response = await agent.call_blackboard(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="hypothesis",
            data=unresolved,
            request_id=write_request_id(
                dispatch_id, "unresolved_after_unavailable_query"
            ),
        )
        return result_entry_id(response, f"dispatch:{dispatch_id}:unresolved")


def build_handler(settings=None, **dependencies):
    """Expose this agent as an A2A task handler."""
    from .runner import build_specialist_handler

    return build_specialist_handler("diagnosis", settings, **dependencies)
