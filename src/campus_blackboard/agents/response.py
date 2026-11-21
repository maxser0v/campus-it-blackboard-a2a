"""Response agent: read shared context, do one task, publish findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import (
    SpecialistRunError,
    entry_data,
    entry_reference,
    knowledge_type,
    result_entry_id,
    reviews_by_revision,
    write_request_id,
)
from .validation import (
    validate_solution,
)

if TYPE_CHECKING:
    from .runner import SpecialistRunner


async def propose_response(
    agent: SpecialistRunner,
    problem_id: str,
    dispatch_id: str,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> tuple[list[str], str]:
    """Use an accepted diagnosis and retrieved runbooks to propose recovery steps."""
    reviews = reviews_by_revision(entries)
    accepted_hypotheses = []
    for entry in entries:
        if (
            knowledge_type(entry) != "hypothesis"
            or entry_data(entry).get("status") != "supported"
        ):
            continue
        ref = (str(entry.get("entry_id")), int(entry.get("revision", 1)))
        accepted = any(
            entry_data(review).get("verdict") == "accept"
            for review in reviews.get(ref, [])
        )
        if accepted:
            accepted_hypotheses.append(entry)
    if not accepted_hypotheses:
        raise SpecialistRunError(
            "response task requires a currently accepted supported hypothesis"
        )
    hypothesis_refs = [
        entry_reference(entry)
        for entry in sorted(
            accepted_hypotheses, key=lambda item: str(item.get("entry_id"))
        )
    ]
    claims = [str(entry_data(entry).get("claim", "")) for entry in accepted_hypotheses]
    runbook_result = agent.tools.execute(
        "search_runbooks", arguments={"query": " ".join(claims)[:500], "limit": 10}
    )
    runbooks = runbook_result.get("results", [])
    runbook_ids = {str(item.get("section_id")) for item in runbooks}
    if not runbooks:
        raise SpecialistRunError(
            "no local runbook sections matched the accepted diagnosis"
        )
    observations = [
        entry for entry in entries if knowledge_type(entry) == "observation"
    ]
    hypothesis_observation_ids = {
        str(ref)
        for hypothesis in accepted_hypotheses
        for ref in entry_data(hypothesis).get("supporting_evidence_ids", [])
    }
    context = {
        "problem": summary,
        "supported_hypotheses": accepted_hypotheses,
        "hypothesis_refs": hypothesis_refs,
        "observations": [
            entry
            for entry in observations
            if str(entry.get("entry_id")) in hypothesis_observation_ids
        ],
        "runbooks": runbooks,
        "_output_schema": {
            "summary": "string",
            "actions": [
                {"step": "string", "purpose": "string", "expected_check": "string"}
            ],
            "caveats": [],
            "evidence_ids": "existing cited observation entry IDs",
            "runbook_ids": "exact retrieved section IDs",
            "hypothesis_refs": hypothesis_refs,
        },
    }
    solution = await agent.ask_model(
        "propose_response",
        context,
        lambda output: validate_solution(output, entries, hypothesis_refs, runbook_ids),
    )
    own_solutions = [
        entry
        for entry in entries
        if knowledge_type(entry) == "solution"
        and entry.get("source_agent") == agent.actor
    ]
    latest_own = (
        sorted(own_solutions, key=lambda item: int(item.get("revision", 1)))[-1]
        if own_solutions
        else None
    )
    if latest_own:
        response = await agent.call_blackboard(
            "update_knowledge",
            problem_id=problem_id,
            entry_id=latest_own["entry_id"],
            expected_revision=int(latest_own.get("revision", 1)),
            knowledge_type="solution",
            data=solution,
            request_id=write_request_id(dispatch_id, "solution"),
        )
        entry_id = result_entry_id(response, str(latest_own["entry_id"]))
    else:
        response = await agent.call_blackboard(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="solution",
            data=solution,
            request_id=write_request_id(dispatch_id, "solution"),
        )
        entry_id = result_entry_id(response, f"dispatch:{dispatch_id}:solution")
    return (
        [entry_id],
        f"Recorded a runbook-backed proposal with {len(solution['actions'])} ordered actions.",
    )


def build_handler(settings=None, **dependencies):
    """Expose this agent as an A2A task handler."""
    from .runner import build_specialist_handler

    return build_specialist_handler("response", settings, **dependencies)
