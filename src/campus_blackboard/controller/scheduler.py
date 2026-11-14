"""Deterministic action selection from the current Blackboard snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


Entry = Mapping[str, Any]
Snapshot = Mapping[str, Any]


@dataclass(frozen=True)
class DispatchPlan:
    role: str
    purpose: str
    dispatch_key: str
    trigger: dict[str, Any]


@dataclass(frozen=True)
class TerminalPlan:
    status: str
    reason: str


def _data(entry: Entry) -> Mapping[str, Any]:
    value = entry.get("data", {})
    return value if isinstance(value, Mapping) else {}


def _kind(entry: Entry) -> str:
    return str(entry.get("knowledge_type", ""))


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _revision_ref(entry: Entry) -> dict[str, Any]:
    return {"entry_id": entry.get("entry_id"), "revision": entry.get("revision")}


def _active_requests(entries: Sequence[Entry]) -> list[Entry]:
    observations = [e for e in entries if _kind(e) == "observation"]
    completed_ids = {
        str(_data(o).get("fulfilled_request_entry_id"))
        for o in observations
        if _data(o).get("fulfilled_request_entry_id")
    }
    requests: list[Entry] = []
    for entry in entries:
        if _kind(entry) != "evidence_request":
            continue
        data = _data(entry)
        status = str(data.get("status", "open")).lower()
        if status in {"fulfilled", "closed", "satisfied", "resolved", "cancelled", "canceled"}:
            continue
        if str(entry.get("entry_id")) in completed_ids:
            continue
        # An unavailable result has already answered the question as far as the
        # fixture/tool catalog permits. Repeating it cannot add information.
        if any(
            str(_data(o).get("fulfilled_request_entry_id")) == str(entry.get("entry_id"))
            and str(_data(o).get("fulfilled_request_status", "")).lower() == "unavailable"
            for o in observations
        ):
            continue
        requests.append(entry)
    return requests


def _reviews_by_target(entries: Sequence[Entry]) -> dict[tuple[str, int], list[Entry]]:
    result: dict[tuple[str, int], list[Entry]] = {}
    for entry in entries:
        if _kind(entry) != "review":
            continue
        data = _data(entry)
        target_id = data.get("target_entry_id")
        target_revision = data.get("target_revision")
        if target_id is None or target_revision is None:
            continue
        try:
            key = (str(target_id), int(target_revision))
        except (TypeError, ValueError):
            continue
        result.setdefault(key, []).append(entry)
    return result


def _latest_decisions(entries: Sequence[Entry]) -> list[Entry]:
    return [
        entry
        for entry in entries
        if _kind(entry) == "controller_decision"
        and str(_data(entry).get("event_type", "")) in {"dispatch_started", "dispatch_finished"}
    ]


def _seen_dispatch_keys(entries: Sequence[Entry]) -> set[str]:
    return {
        str(_data(entry).get("dispatch_key"))
        for entry in _latest_decisions(entries)
        if _data(entry).get("dispatch_key")
    }


def _sort_key(entry: Entry) -> tuple[str, int, str]:
    try:
        revision = int(entry.get("revision", 1))
    except (TypeError, ValueError):
        revision = 1
    return (str(entry.get("created_at", "")), revision, str(entry.get("entry_id", "")))


def _latest_review(matched_reviews: Sequence[Entry]) -> Entry | None:
    return sorted(matched_reviews, key=_sort_key)[-1] if matched_reviews else None


def _latest_verdict(matched_reviews: Sequence[Entry]) -> str | None:
    """Use the newest review of one exact revision when reviews disagree."""
    latest = _latest_review(matched_reviews)
    verdict = str(_data(latest).get("verdict", "")).lower() if latest is not None else ""
    return verdict or None


def _accepted(entry: Entry, reviews: Mapping[tuple[str, int], list[Entry]]) -> bool:
    matched = reviews.get((str(entry.get("entry_id")), int(entry.get("revision", 1))), [])
    return _latest_verdict(matched) == "accept"


def _ref_set(value: Any) -> set[tuple[str, int]]:
    if not isinstance(value, list):
        return set()
    refs: set[tuple[str, int]] = set()
    for ref in value:
        if not isinstance(ref, Mapping) or ref.get("entry_id") is None:
            continue
        try:
            refs.add((str(ref["entry_id"]), int(ref.get("revision", 1))))
        except (TypeError, ValueError):
            continue
    return refs


def _response_matches_hypotheses(
    solution: Entry,
    viable: Sequence[Entry],
    entries: Sequence[Entry],
) -> bool:
    expected_refs = {(str(h.get("entry_id")), int(h.get("revision", 1))) for h in viable}
    solution_refs = _ref_set(_data(solution).get("hypothesis_refs"))
    if solution_refs:
        return solution_refs == expected_refs

    response_starts = [
        e for e in entries
        if _kind(e) == "controller_decision"
        and _data(e).get("event_type") == "dispatch_started"
        and _data(e).get("agent") == "response"
    ]
    if not response_starts:
        # A board created outside this controller may not carry response
        # provenance. The exact solution review remains the available gate.
        return True
    latest_start = sorted(response_starts, key=_sort_key)[-1]
    trigger_refs = _ref_set(_data(latest_start).get("trigger", {}).get("hypotheses"))
    if trigger_refs != expected_refs:
        return False
    dispatch_id = _data(latest_start).get("dispatch_id")
    matching_result = any(
        _kind(e) == "controller_decision"
        and _data(e).get("event_type") == "dispatch_finished"
        and _data(e).get("dispatch_id") == dispatch_id
        and str(solution.get("entry_id")) in {str(ref) for ref in _data(e).get("result_entry_ids", [])}
        for e in entries
    )
    return matching_result


def _make_dispatch(
    role: str,
    purpose: str,
    semantic_input: Any,
    trigger: dict[str, Any],
    seen: set[str],
) -> DispatchPlan | None:
    key = f"{role}:{purpose}:{_stable_digest(semantic_input)}"
    if key in seen:
        return None
    return DispatchPlan(role=role, purpose=purpose, dispatch_key=key, trigger=trigger)


def _choose_next_action(
    snapshot: Snapshot,
    *,
    dispatch_count: int = 0,
    max_dispatches: int = 12,
) -> DispatchPlan | TerminalPlan:
    """Choose one useful action, or a truthful terminal state.

    The function is pure so the same board snapshot always yields the same
    decision. Dispatch signatures use semantic entries, not read/audit events.
    """

    problem = snapshot.get("problem", {})
    entries_value = snapshot.get("entries", [])
    entries = [e for e in entries_value if isinstance(e, Mapping)]
    seen = _seen_dispatch_keys(entries)

    observations = sorted(
        (e for e in entries if _kind(e) == "observation"),
        key=lambda e: (str(e.get("entry_id")), int(e.get("revision", 1))),
    )
    requests = _active_requests(entries)

    # An outstanding information request takes priority over general analysis.
    # The specialist chooses only from its allowlisted fixture tools.
    if requests:
        request = requests[0]
        request_ref = _revision_ref(request)
        plan = _make_dispatch(
            "evidence",
            "collect_evidence",
            {"problem": problem.get("problem_id"), "request": request_ref},
            {"rule": "open_evidence_request", "request": request_ref},
            seen,
        )
        if plan is not None:
            return plan
        # A request can be unavailable. _active_requests removes it after the
        # linked observation records that result, so a repeated key here means
        # the specialist ran but failed to record any outcome.
        return TerminalPlan("unresolved", "evidence_request_not_satisfied")

    if not observations:
        plan = _make_dispatch(
            "evidence",
            "collect_evidence",
            {"problem": problem.get("problem_id"), "kind": "initial"},
            {"rule": "no_observations", "board_revision": problem.get("board_revision")},
            seen,
        )
        if plan is not None:
            return plan
        return TerminalPlan("unresolved", "no_observations_available")

    hypotheses = [e for e in entries if _kind(e) == "hypothesis"]
    reviews = _reviews_by_target(entries)
    expected_observations = _ref_set([_revision_ref(e) for e in observations])
    fresh_hypotheses = [
        h for h in hypotheses
        if _ref_set(_data(h).get("observation_refs")) == expected_observations
    ]
    accepted_hypotheses = [h for h in fresh_hypotheses if _accepted(h, reviews)]
    current_hypothesis_revisions = {
        (str(h.get("entry_id")), int(h.get("revision", 1))) for h in fresh_hypotheses
    }
    rejected_reviews = []
    for target_id, target_revision in current_hypothesis_revisions:
        matched = reviews.get((target_id, target_revision), [])
        latest = _latest_review(matched)
        if latest is not None and _latest_verdict(matched) == "reject":
            rejected_reviews.append(latest)

    # Revisit diagnoses when the evidence set changes or a reviewer rejects a
    # current hypothesis. A single diagnostic pass is allowed for each semantic
    # evidence/rejection state.
    evidence_signature = [_revision_ref(e) for e in observations]
    rejection_signature = [
        {
            "entry_id": r.get("entry_id"),
            "revision": r.get("revision"),
            "target_entry_id": _data(r).get("target_entry_id"),
            "target_revision": _data(r).get("target_revision"),
        }
        for r in sorted(rejected_reviews, key=lambda e: str(e.get("entry_id")))
    ]
    considered_observation_ids = {
        str(ref)
        for hypothesis in hypotheses
        for field in ("supporting_evidence_ids", "contradicting_evidence_ids")
        for ref in (_data(hypothesis).get(field, []) or [])
    }
    uncovered_observation_ids = sorted(
        str(entry.get("entry_id"))
        for entry in observations
        if str(entry.get("entry_id")) not in considered_observation_ids
    )
    # A fresh diagnostic pass may retain older competing findings for history.
    # Those findings cannot authorize a response, but must not prevent a new
    # unresolved finding from reaching review and requesting missing evidence.
    stale_evidence = bool(hypotheses) and not fresh_hypotheses
    if not hypotheses or uncovered_observation_ids or rejection_signature or stale_evidence:
        purpose = "diagnose"
        plan = _make_dispatch(
            "diagnosis",
            purpose,
            {"observations": evidence_signature, "rejections": rejection_signature},
            {
                "rule": (
                    "no_hypotheses"
                    if not hypotheses
                    else "review_rejected_hypothesis"
                    if rejection_signature
                    else "new_observation_not_considered"
                    if uncovered_observation_ids
                    else "observation_revision_changed"
                ),
                "observations": evidence_signature,
                "uncovered_observation_ids": uncovered_observation_ids,
                "reviews": rejection_signature,
            },
            seen,
        )
        if plan is not None:
            return plan
        if stale_evidence:
            return TerminalPlan("unresolved", "diagnosis_has_stale_evidence")

    # Every current hypothesis revision must receive its own review. An older
    # review never approves a newly revised logical entry.
    review_candidates = [
        h for h in fresh_hypotheses if not reviews.get((str(h.get("entry_id")), int(h.get("revision", 1))))
    ]
    if review_candidates:
        hypothesis = sorted(review_candidates, key=lambda h: str(h.get("entry_id")))[0]
        ref = _revision_ref(hypothesis)
        plan = _make_dispatch(
            "review",
            "review_hypotheses",
            ref,
            {"rule": "unreviewed_hypothesis_revision", "hypothesis": ref},
            seen,
        )
        if plan is not None:
            return plan
        return TerminalPlan("unresolved", "hypothesis_review_did_not_produce_a_review")

    # A supported diagnosis that has been accepted at its exact current revision
    # is the only state that permits a response proposal.
    viable = sorted(
        (
            h
            for h in accepted_hypotheses
            if str(_data(h).get("status", "")).lower() == "supported"
        ),
        key=_sort_key,
    )
    solutions = [e for e in entries if _kind(e) == "solution"]
    current_solutions = sorted(solutions, key=_sort_key)
    latest_solution = current_solutions[-1] if current_solutions else None
    latest_solution_reviews = (
        reviews.get(
            (str(latest_solution.get("entry_id")), int(latest_solution.get("revision", 1))), []
        )
        if latest_solution
        else []
    )
    rejected_solution_reviews = []
    latest_solution_review = _latest_review(latest_solution_reviews)
    if latest_solution_review and _latest_verdict(latest_solution_reviews) == "reject":
        rejected_solution_reviews.append(latest_solution_review)
    if latest_solution is not None and _latest_verdict(latest_solution_reviews) == "accept":
        solution_data = _data(latest_solution)
        solution_evidence_ids = list(solution_data.get("evidence_ids", []))
        known_observation_ids = {str(e.get("entry_id")) for e in observations}
        if (
            viable
            and _response_matches_hypotheses(latest_solution, viable, entries)
            and solution_evidence_ids
            and all(str(ref) in known_observation_ids for ref in solution_evidence_ids)
            and bool(solution_data.get("runbook_ids"))
        ):
            return TerminalPlan("completed", "current_supported_diagnosis_and_solution_accepted")
    if viable:
        refs = [_revision_ref(h) for h in viable]
        rejected_solution_refs = [
            {"entry_id": r.get("entry_id"), "revision": r.get("revision")}
            for r in rejected_solution_reviews
        ]
        plan = _make_dispatch(
            "response",
            "propose_response",
            {"hypotheses": refs, "rejected_solution_reviews": rejected_solution_refs},
            {
                "rule": "response_rejected"
                if rejected_solution_refs
                else "accepted_supported_hypothesis",
                "hypotheses": refs,
                "rejected_solution_reviews": rejected_solution_refs,
            },
            seen,
        )
        if plan is not None:
            return plan
        if latest_solution is None:
            return TerminalPlan("unresolved", "response_proposal_missing")

    # Review each response revision independently. Only an accepted review of
    # the current solution can satisfy the final completion gate.
    if latest_solution is not None:
        solution = latest_solution
        solution_ref = _revision_ref(solution)
        matched_reviews = reviews.get(
            (str(solution.get("entry_id")), int(solution.get("revision", 1))), []
        )
        if not matched_reviews:
            plan = _make_dispatch(
                "review",
                "review_solution",
                solution_ref,
                {"rule": "unreviewed_solution_revision", "solution": solution_ref},
                seen,
            )
            if plan is not None:
                return plan
            return TerminalPlan("unresolved", "solution_review_did_not_produce_a_review")
        if _latest_verdict(matched_reviews) == "accept":
            solution_data = _data(solution)
            cited_ids = list(solution_data.get("evidence_ids", []))
            known_observation_ids = {str(e.get("entry_id")) for e in observations}
            if (
                viable
                and _response_matches_hypotheses(solution, viable, entries)
                and cited_ids
                and all(str(ref) in known_observation_ids for ref in cited_ids)
                and bool(solution_data.get("runbook_ids"))
            ):
                return TerminalPlan("completed", "current_supported_diagnosis_and_solution_accepted")
            return TerminalPlan("unresolved", "accepted_solution_has_incomplete_references")

    # When no rule can make progress, conclude honestly instead of cycling on
    # stale or unsupported content.
    if rejected_reviews:
        return TerminalPlan("unresolved", "hypotheses_rejected_without_new_evidence")
    if hypotheses and not viable:
        return TerminalPlan("unresolved", "no_supported_reviewed_hypothesis")
    if solutions:
        return TerminalPlan("unresolved", "solution_not_accepted")
    return TerminalPlan("unresolved", "no_eligible_action")


def choose_next_action(
    snapshot: Snapshot, *, dispatch_count: int = 0, max_dispatches: int = 12,
) -> DispatchPlan | TerminalPlan:
    """Assess completion before enforcing the limit on further specialist work."""
    plan = _choose_next_action(snapshot)
    if isinstance(plan, TerminalPlan) and plan.status == "completed":
        return plan
    if dispatch_count >= max_dispatches:
        return TerminalPlan("unresolved", "dispatch_limit_reached")
    return plan
