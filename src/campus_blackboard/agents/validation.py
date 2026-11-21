"""Check model outputs against actual evidence, ownership and exact revisions.

These checks run after the model call, before any findings are published.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ..tools.fixtures import validate_fixture_arguments
from .common import (
    FIXTURE_TOOL_NAMES,
    HYPOTHESIS_STATES,
    REVIEW_VERDICTS,
    entry_data,
    knowledge_type,
)


def meaningful_tokens(value: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "may",
        "might",
        "could",
        "would",
        "should",
        "are",
        "was",
        "were",
        "have",
        "has",
        "been",
        "being",
        "into",
        "while",
        "when",
        "where",
        "which",
        "what",
        "who",
        "how",
        "not",
        "yet",
        "only",
        "current",
        "observed",
        "affected",
        "service",
        "request",
        "requests",
        "evidence",
        "available",
        "high",
        "low",
        "is",
        "an",
        "a",
        "of",
        "to",
        "in",
        "on",
        "at",
        "as",
        "by",
        "it",
        "their",
        "its",
        "from",
    }
    terms = {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in stop
    }
    aliases = {
        "database": "db",
        "connections": "connection",
        "connection": "connection",
        "resolvers": "resolver",
        "resolution": "dns",
        "name": "dns",
        "nxdomain": "nxdomain",
    }
    return {aliases.get(token, token.rstrip("s")) for token in terms}


def observation_text(entry: Mapping[str, Any]) -> str:
    data = entry_data(entry)
    if not data.get("records"):
        return ""
    return json.dumps(data["records"], ensure_ascii=False, sort_keys=True).lower()


def has_grounding(claim: str, cited_observations: list[Mapping[str, Any]]) -> bool:
    """Require raw returned records with relevant signal words, not just IDs."""
    tokens = meaningful_tokens(claim)
    if not tokens:
        return False
    signal_groups = [
        (
            {"database", "db", "pool", "connection", "acquisition", "occupancy"},
            {"db", "pool", "connection"},
        ),
        (
            {"dns", "resolver", "nxdomain", "resolution", "authoritative", "hostname"},
            {"dns", "resolver", "nxdomain"},
        ),
        (
            {"traffic", "demand", "load", "request", "rate"},
            {"traffic", "request", "rate"},
        ),
    ]
    normalized_corpus = " ".join(
        observation_text(entry) for entry in cited_observations
    )
    if not normalized_corpus.strip():
        return False
    corpus_tokens = meaningful_tokens(normalized_corpus)
    topic = next(
        (expected for claim_terms, expected in signal_groups if tokens & claim_terms),
        None,
    )
    # Generic HTTP errors, latency and request rates describe symptoms. They
    # cannot establish which dependency failed or why it failed.
    records = [
        row
        for entry in cited_observations
        for row in entry_data(entry).get("records", [])
        if isinstance(row, Mapping)
    ]
    if tokens & {"upstream", "downstream", "dependency", "network"}:
        identified = any(
            isinstance(row.get("fields"), Mapping)
            and any(
                row["fields"].get(key)
                for key in (
                    "dependency",
                    "upstream_service",
                    "upstream_host",
                    "cause",
                    "root_cause",
                )
            )
            for row in records
        )
        if not identified:
            return False
    if topic is not None:
        return bool(corpus_tokens & topic)
    symptom_terms = {
        "latency",
        "error",
        "failed",
        "failure",
        "upstream",
        "downstream",
        "dependency",
        "intermittent",
        "elevated",
        "high",
        "request",
        "rate",
        "response",
        "502",
        "causing",
        "cause",
        "slow",
        "service",
    }
    overlap = (tokens & corpus_tokens) - symptom_terms
    return len(overlap) >= min(2, max(1, len(tokens) // 3))


def validate_tool_choice(
    output: Any, open_requests: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    tool = output.get("tool")
    query = output.get("query")
    rationale = output.get("rationale")
    if tool not in FIXTURE_TOOL_NAMES or not isinstance(query, dict):
        raise ValueError(
            "tool must be an allowlisted fixture query with an object query"
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale must be a non-empty string")
    validate_fixture_arguments(tool, query)
    if open_requests:
        request_id = output.get("fulfilled_request_entry_id")
        selected = next(
            (item for item in open_requests if item.get("entry_id") == request_id), None
        )
        if selected is None:
            raise ValueError("an open request must be selected by its entry ID")
        if tool != selected.get("requested_tool") or query != selected.get("query"):
            raise ValueError(
                "the selected tool and query must exactly fulfill the chosen open request"
            )
    return {
        "tool": tool,
        "query": query,
        "rationale": rationale.strip(),
        "fulfilled_request_entry_id": output.get("fulfilled_request_entry_id"),
    }


def validate_evidence_request(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("evidence request must be an object")
    question, reason, tool, query = (
        item.get(key) for key in ("question", "reason", "requested_tool", "query")
    )
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ValueError("evidence request question must be a non-empty short string")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("evidence request reason must be a non-empty short string")
    if tool not in FIXTURE_TOOL_NAMES or not isinstance(query, dict):
        raise ValueError(
            "evidence request must name an allowlisted query tool and object query"
        )
    # Apply the same argument allowlist as Evidence Agent, with a synthetic
    # query-choice wrapper so no arbitrary tool argument can reach execution.
    validate_tool_choice({"tool": tool, "query": query, "rationale": reason}, [])
    if item.get("status", "open") != "open":
        raise ValueError("new evidence requests must have status 'open'")
    return {
        "question": question.strip(),
        "requested_tool": tool,
        "query": query,
        "reason": reason.strip(),
        "status": "open",
    }


def validate_diagnosis(
    output: Any, entries: list[dict[str, Any]], actor: str
) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    hypotheses = output.get("hypotheses")
    requests = output.get("evidence_requests")
    if (
        not isinstance(hypotheses, list)
        or len(hypotheses) > 5
        or not isinstance(requests, list)
        or len(requests) > 3
    ):
        raise ValueError("hypotheses and evidence_requests must be bounded arrays")
    observations = {
        str(entry.get("entry_id")): entry
        for entry in entries
        if knowledge_type(entry) == "observation"
    }
    own_hypotheses = {
        str(entry.get("entry_id")): entry
        for entry in entries
        if knowledge_type(entry) == "hypothesis" and entry.get("source_agent") == actor
    }
    clean_hypotheses = []
    for item in hypotheses:
        if not isinstance(item, dict):
            raise ValueError("each hypothesis must be an object")
        claim = item.get("claim")
        status = item.get("status")
        if (
            not isinstance(claim, str)
            or not claim.strip()
            or len(claim) > 700
            or status not in HYPOTHESIS_STATES
        ):
            raise ValueError("hypothesis needs a short claim and permitted status")
        supporting = item.get("supporting_evidence_ids", [])
        contradicting = item.get("contradicting_evidence_ids", [])
        questions = item.get("open_questions", [])
        if (
            not isinstance(supporting, list)
            or not isinstance(contradicting, list)
            or not isinstance(questions, list)
        ):
            raise ValueError("hypothesis references and open_questions must be arrays")
        if any(str(ref) not in observations for ref in supporting + contradicting):
            raise ValueError(
                "hypothesis references an unknown or non-observation entry ID"
            )
        if any(
            not isinstance(question, str) or not question.strip()
            for question in questions
        ):
            raise ValueError("hypothesis open questions must be non-empty strings")
        if status == "supported":
            cited = [observations[str(ref)] for ref in supporting]
            if not cited or not has_grounding(claim, cited):
                raise ValueError(
                    "supported hypothesis requires non-empty, claim-relevant, cause-specific observation records; generic latency, error rates and HTTP 502/upstream errors alone do not identify a root cause"
                )
        revise_id = item.get("revise_entry_id")
        if revise_id is not None:
            prior = own_hypotheses.get(str(revise_id))
            if prior is None:
                raise ValueError(
                    "revise_entry_id must identify a current hypothesis owned by this agent"
                )
        clean_hypotheses.append(
            {
                "claim": claim.strip(),
                "status": status,
                "supporting_evidence_ids": [str(ref) for ref in supporting],
                "contradicting_evidence_ids": [str(ref) for ref in contradicting],
                "open_questions": [q.strip() for q in questions],
                "revise_entry_id": str(revise_id) if revise_id is not None else None,
            }
        )
    return {
        "hypotheses": clean_hypotheses,
        "evidence_requests": [validate_evidence_request(item) for item in requests],
    }


def validate_review(
    output: Any,
    target: dict[str, Any],
    entries: list[dict[str, Any]],
    purpose: str,
    retrieved_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    if output.get("target_entry_id") != target.get("entry_id") or output.get(
        "target_revision"
    ) != target.get("revision"):
        raise ValueError("review must target the exact entry revision supplied")
    verdict = output.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        raise ValueError("verdict must be accept, reject, or request_evidence")
    rationale = output.get("rationale")
    citations = output.get("supporting_evidence_ids", [])
    if (
        not isinstance(rationale, str)
        or not rationale.strip()
        or not isinstance(citations, list)
    ):
        raise ValueError("review needs a rationale and evidence ID array")
    observation_map = {
        str(entry.get("entry_id")): entry
        for entry in entries
        if knowledge_type(entry) == "observation"
    }
    if any(str(ref) not in observation_map for ref in citations):
        raise ValueError("review cites an unknown or non-observation entry")
    data = entry_data(target)
    if verdict == "accept":
        if purpose == "review_hypotheses":
            if data.get("status") != "supported":
                raise ValueError("only a supported hypothesis can be accepted")
            hypothesis_refs = {
                str(ref) for ref in data.get("supporting_evidence_ids", [])
            }
            cited = [
                observation_map[str(ref)]
                for ref in citations
                if str(ref) in hypothesis_refs
            ]
            if not cited or not has_grounding(str(data.get("claim", "")), cited):
                raise ValueError(
                    "review cannot accept a claim without relevant, non-empty cited records"
                )
        else:
            solution_evidence = {str(ref) for ref in data.get("evidence_ids", [])}
            if (
                not data.get("actions")
                or not set(data.get("runbook_ids", [])).issubset(retrieved_ids)
                or not data.get("runbook_ids")
            ):
                raise ValueError(
                    "response cannot be accepted without actions and retrieved runbook sections"
                )
            cited = [
                observation_map[str(ref)]
                for ref in citations
                if str(ref) in solution_evidence
            ]
            if not cited or any(not observation_text(entry) for entry in cited):
                raise ValueError(
                    "response cannot be accepted without non-empty cited observation records"
                )
    follow_up = output.get("required_follow_up")
    if verdict == "request_evidence":
        follow_up = validate_evidence_request({**(follow_up or {}), "status": "open"})
    elif follow_up is not None:
        raise ValueError(
            "required_follow_up must be null unless verdict requests evidence"
        )
    return {
        "target_entry_id": target["entry_id"],
        "target_revision": int(target["revision"]),
        "verdict": verdict,
        "rationale": rationale.strip(),
        "supporting_evidence_ids": [str(ref) for ref in citations],
        "required_follow_up": follow_up,
    }


def validate_solution(
    output: Any,
    entries: list[dict[str, Any]],
    expected_hypothesis_refs: list[dict[str, Any]],
    retrieved_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    summary = output.get("summary")
    actions = output.get("actions")
    caveats = output.get("caveats", [])
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1200:
        raise ValueError("solution needs a non-empty concise summary")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 5:
        raise ValueError("solution needs between one and five ordered actions")
    clean_actions = []
    for action in actions:
        if not isinstance(action, dict) or any(
            not isinstance(action.get(key), str) or not action[key].strip()
            for key in ("step", "purpose", "expected_check")
        ):
            raise ValueError(
                "each action requires step, purpose, and expected_check text"
            )
        clean_actions.append(
            {key: action[key].strip() for key in ("step", "purpose", "expected_check")}
        )
    if not isinstance(caveats, list) or any(
        not isinstance(item, str) or not item.strip() for item in caveats
    ):
        raise ValueError("caveats must be an array of non-empty strings")
    observation_ids = {
        str(entry.get("entry_id"))
        for entry in entries
        if knowledge_type(entry) == "observation"
    }
    evidence_ids = output.get("evidence_ids", [])
    runbook_ids = output.get("runbook_ids", [])
    if not isinstance(evidence_ids, list) or any(
        str(ref) not in observation_ids for ref in evidence_ids
    ):
        raise ValueError("solution must cite existing observation entry IDs")
    if (
        not isinstance(runbook_ids, list)
        or not runbook_ids
        or any(ref not in retrieved_ids for ref in runbook_ids)
    ):
        raise ValueError(
            "solution must cite exact section IDs returned by runbook retrieval"
        )
    cited_hypotheses = {
        str(ref)
        for hypothesis_ref in expected_hypothesis_refs
        for ref in [hypothesis_ref.get("entry_id")]
    }
    allowed_evidence = {
        str(ref)
        for entry in entries
        if knowledge_type(entry) == "hypothesis"
        and str(entry.get("entry_id")) in cited_hypotheses
        for ref in entry_data(entry).get("supporting_evidence_ids", [])
    }
    if not evidence_ids or not set(map(str, evidence_ids)).issubset(allowed_evidence):
        raise ValueError(
            "solution must cite non-empty evidence used by the accepted diagnosis"
        )
    return {
        "summary": summary.strip(),
        "actions": clean_actions,
        "caveats": [item.strip() for item in caveats],
        "evidence_ids": [str(ref) for ref in evidence_ids],
        "runbook_ids": list(runbook_ids),
        "hypothesis_refs": expected_hypothesis_refs,
    }
