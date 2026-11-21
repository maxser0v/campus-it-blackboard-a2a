"""Review agent: read shared context, do one task, publish findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .common import (
    FIXTURE_TOOL_NAMES,
    REVIEW_VERDICTS,
    SpecialistRunError,
    entry_data,
    knowledge_type,
    open_evidence_requests,
    result_entry_id,
    reviews_by_revision,
    same_request,
    unavailable_queries,
    was_unavailable,
    write_request_id,
)
from .validation import (
    validate_review,
)

if TYPE_CHECKING:
    from .runner import SpecialistRunner


async def review(
    agent: SpecialistRunner,
    problem_id: str,
    purpose: str,
    dispatch_id: str,
    summary: dict[str, Any],
    entries: list[dict[str, Any]],
) -> tuple[list[str], str]:
    """Select a current revision, check its evidence, and publish a verdict."""
    target = select_review_target(agent, entries, purpose)
    if target is None:
        raise SpecialistRunError(f"no current entry is eligible for {purpose}")
    observations = [
        entry for entry in entries if knowledge_type(entry) == "observation"
    ]
    retrieved: list[dict[str, Any]] = []
    if purpose == "review_solution":
        solution_data = entry_data(target)
        query = f"{solution_data.get('summary', '')} " + " ".join(
            str(action.get("step", ""))
            for action in solution_data.get("actions", [])
            if isinstance(action, Mapping)
        )
        result = agent.tools.execute(
            "search_runbooks", arguments={"query": query[:500], "limit": 10}
        )
        retrieved = result.get("results", [])
    retrieved_ids = {str(item.get("section_id")) for item in retrieved}
    context = {
        "problem": summary,
        "purpose": purpose,
        "target": target,
        "observations": observations,
        "observation_entry_ids": [entry.get("entry_id") for entry in observations],
        "open_evidence_requests": open_evidence_requests(entries),
        "unavailable_evidence_queries": unavailable_queries(entries),
        "retrieved_runbooks": retrieved,
        "retrieved_runbook_ids": sorted(retrieved_ids),
        "_output_schema": {
            "target_entry_id": "exact target entry ID",
            "target_revision": "exact target revision",
            "verdict": sorted(REVIEW_VERDICTS),
            "rationale": "string",
            "supporting_evidence_ids": [],
            "required_follow_up": {
                "question": "nonempty string",
                "requested_tool": sorted(FIXTURE_TOOL_NAMES),
                "query": "object containing ONLY arguments listed in available_tools for the selected tool",
                "reason": "nonempty string",
            },
        },
    }
    review = await agent.ask_model(
        purpose,
        context,
        lambda output: validate_review(output, target, entries, purpose, retrieved_ids),
    )
    follow_up = review.get("required_follow_up")
    if (
        review["verdict"] == "request_evidence"
        and isinstance(follow_up, Mapping)
        and was_unavailable(
            follow_up.get("requested_tool"),
            follow_up.get("query"),
            context["unavailable_evidence_queries"],
        )
    ):
        review["verdict"] = "reject"
        review["required_follow_up"] = None
        review["rationale"] = (
            str(review["rationale"]).rstrip()
            + " The same tool query already returned no records for this fixture, so the duplicate request was suppressed."
        )
    review_data = {
        key: review[key]
        for key in (
            "target_entry_id",
            "target_revision",
            "verdict",
            "rationale",
            "supporting_evidence_ids",
            "required_follow_up",
        )
    }
    response = await agent.call_blackboard(
        "write_blackboard",
        problem_id=problem_id,
        knowledge_type="review",
        data=review_data,
        request_id=write_request_id(dispatch_id, "review"),
    )
    published_ids = [result_entry_id(response, f"dispatch:{dispatch_id}:review")]
    follow_up = review.get("required_follow_up")
    if review["verdict"] == "request_evidence" and follow_up:
        active = [
            entry
            for entry in entries
            if knowledge_type(entry) == "evidence_request"
            and str(entry_data(entry).get("status", "open")).lower()
            in {"open", "pending"}
        ]
        if not any(same_request(follow_up, entry) for entry in active):
            request_response = await agent.call_blackboard(
                "write_blackboard",
                problem_id=problem_id,
                knowledge_type="evidence_request",
                data=follow_up,
                request_id=write_request_id(dispatch_id, "review_evidence_request"),
            )
            published_ids.append(
                result_entry_id(
                    request_response, f"dispatch:{dispatch_id}:evidence_request"
                )
            )
        else:
            existing = next(entry for entry in active if same_request(follow_up, entry))
            published_ids.append(str(existing.get("entry_id")))
    return (
        published_ids,
        f"{review['verdict']} review for {target['entry_id']} revision {target['revision']}.",
    )


def select_review_target(
    agent: SpecialistRunner, entries: list[dict[str, Any]], purpose: str
) -> dict[str, Any] | None:
    reviewed = reviews_by_revision(entries)
    if purpose == "review_hypotheses":
        current_observations = {
            (str(entry.get("entry_id")), int(entry.get("revision", 1)))
            for entry in entries
            if knowledge_type(entry) == "observation"
        }

        def current_context(entry: Mapping[str, Any]) -> bool:
            refs = entry_data(entry).get("observation_refs")
            if not isinstance(refs, list):
                return False
            try:
                versions = {
                    (str(ref["entry_id"]), int(ref["revision"])) for ref in refs
                }
            except (KeyError, TypeError, ValueError):
                return False
            return versions == current_observations

        candidates = [
            entry
            for entry in entries
            if knowledge_type(entry) == "hypothesis"
            and current_context(entry)
            and (str(entry.get("entry_id")), int(entry.get("revision", 1)))
            not in reviewed
        ]
    else:
        candidates = [
            entry
            for entry in entries
            if knowledge_type(entry) == "solution"
            and (str(entry.get("entry_id")), int(entry.get("revision", 1)))
            not in reviewed
        ]
        actor_solutions = [
            entry for entry in candidates if entry.get("source_agent") == agent.actor
        ]
        if actor_solutions:
            candidates = actor_solutions
    return (
        sorted(
            candidates,
            key=lambda entry: (
                entry_data(entry).get("status") != "supported",
                str(entry.get("entry_id")),
            ),
        )[0]
        if candidates
        else None
    )


def build_handler(settings=None, **dependencies):
    """Expose this agent as an A2A task handler."""
    from .runner import build_specialist_handler

    return build_specialist_handler("review", settings, **dependencies)
