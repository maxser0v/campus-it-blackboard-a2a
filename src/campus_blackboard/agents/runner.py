"""Blackboard-backed specialist roles with strict outputs and grounded citations."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from ..langchain_adapter import specialist_chain
from ..model_adapter import ModelAdapter, model_from_settings
from ..tools import ToolRegistry, read_case_summary
from ..tools.fixtures import read_query_catalog, validate_fixture_arguments
from ..tools.queries import already_observed, signature


ROLES = frozenset({"evidence", "diagnosis", "review", "response"})
PURPOSES = {
    "evidence": frozenset({"collect_evidence"}),
    "diagnosis": frozenset({"diagnose"}),
    "review": frozenset({"review_hypotheses", "review_solution"}),
    "response": frozenset({"propose_response"}),
}
_PROMPT_ROOT = Path(__file__).resolve().parents[3] / "prompts"
_HYPOTHESIS_STATES = {"proposed", "supported", "weakened", "rejected", "unresolved"}
_REVIEW_VERDICTS = {"accept", "reject", "request_evidence"}
_FIXTURE_TOOL_NAMES = {"query_metrics", "query_logs", "get_change_history"}


class SpecialistRunError(RuntimeError):
    """An explicit failure from an agent, model, tool, or Blackboard call."""


def _prompt(role: str) -> str:
    path = _PROMPT_ROOT / f"{role}.md"
    return path.read_text(encoding="utf-8")


def _entries(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = snapshot.get("entries", [])
    if not isinstance(value, list):
        raise SpecialistRunError("Blackboard snapshot has no entries array")
    return [dict(entry) for entry in value if isinstance(entry, Mapping)]


def _kind(entry: Mapping[str, Any]) -> str:
    return str(entry.get("knowledge_type", ""))


def _data(entry: Mapping[str, Any]) -> dict[str, Any]:
    value = entry.get("data", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _ref(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {"entry_id": str(entry.get("entry_id", "")), "revision": int(entry.get("revision", 1))}


def _deterministic_id(dispatch_id: str, suffix: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"campus-blackboard:{dispatch_id}:{suffix}"))


def _result_entry_id(result: Mapping[str, Any], fallback: str) -> str:
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


def _is_error(result: Mapping[str, Any]) -> bool:
    return bool(result.get("error")) or result.get("ok") is False


def _meaningful_tokens(value: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "may", "might", "could", "would",
        "should", "are", "was", "were", "have", "has", "been", "being", "into", "while", "when",
        "where", "which", "what", "who", "how", "not", "yet", "only", "current", "observed",
        "affected", "service", "request", "requests", "evidence", "available", "high", "low", "is",
        "an", "a", "of", "to", "in", "on", "at", "as", "by", "it", "their", "its", "from",
    }
    terms = {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in stop}
    aliases = {"database": "db", "connections": "connection", "connection": "connection", "resolvers": "resolver", "resolution": "dns", "name": "dns", "nxdomain": "nxdomain"}
    return {aliases.get(token, token.rstrip("s")) for token in terms}


def _observation_text(entry: Mapping[str, Any]) -> str:
    data = _data(entry)
    if not data.get("records"):
        return ""
    return json.dumps(data["records"], ensure_ascii=False, sort_keys=True).lower()


def _has_grounding(claim: str, cited_observations: list[Mapping[str, Any]]) -> bool:
    """Require raw returned records with relevant signal words, not just IDs."""
    tokens = _meaningful_tokens(claim)
    if not tokens:
        return False
    signal_groups = [
        ({"database", "db", "pool", "connection", "acquisition", "occupancy"}, {"db", "pool", "connection"}),
        ({"dns", "resolver", "nxdomain", "resolution", "authoritative", "hostname"}, {"dns", "resolver", "nxdomain"}),
        ({"traffic", "demand", "load", "request", "rate"}, {"traffic", "request", "rate"}),
    ]
    normalized_corpus = " ".join(_observation_text(entry) for entry in cited_observations)
    if not normalized_corpus.strip():
        return False
    corpus_tokens = _meaningful_tokens(normalized_corpus)
    topic = next((expected for claim_terms, expected in signal_groups if tokens & claim_terms), None)
    # Generic HTTP errors, latency and request rates describe symptoms. They
    # cannot establish which dependency failed or why it failed.
    records = [row for entry in cited_observations for row in _data(entry).get("records", []) if isinstance(row, Mapping)]
    if tokens & {"upstream", "downstream", "dependency", "network"}:
        identified = any(
            isinstance(row.get("fields"), Mapping)
            and any(row["fields"].get(key) for key in ("dependency", "upstream_service", "upstream_host", "cause", "root_cause"))
            for row in records
        )
        if not identified:
            return False
    if topic is not None:
        return bool(corpus_tokens & topic)
    symptom_terms = {"latency", "error", "failed", "failure", "upstream", "downstream", "dependency", "intermittent", "elevated", "high", "request", "rate", "response", "502", "causing", "cause", "slow", "service"}
    overlap = (tokens & corpus_tokens) - symptom_terms
    return len(overlap) >= min(2, max(1, len(tokens) // 3))


def _validate_tool_choice(output: Any, open_requests: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    tool = output.get("tool")
    query = output.get("query")
    rationale = output.get("rationale")
    if tool not in _FIXTURE_TOOL_NAMES or not isinstance(query, dict):
        raise ValueError("tool must be an allowlisted fixture query with an object query")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("rationale must be a non-empty string")
    validate_fixture_arguments(tool, query)
    if open_requests:
        request_id = output.get("fulfilled_request_entry_id")
        selected = next((item for item in open_requests if item.get("entry_id") == request_id), None)
        if selected is None:
            raise ValueError("an open request must be selected by its entry ID")
        if tool != selected.get("requested_tool") or query != selected.get("query"):
            raise ValueError("the selected tool and query must exactly fulfill the chosen open request")
    return {"tool": tool, "query": query, "rationale": rationale.strip(), "fulfilled_request_entry_id": output.get("fulfilled_request_entry_id")}


def _validate_request(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("evidence request must be an object")
    question, reason, tool, query = (item.get(key) for key in ("question", "reason", "requested_tool", "query"))
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        raise ValueError("evidence request question must be a non-empty short string")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("evidence request reason must be a non-empty short string")
    if tool not in _FIXTURE_TOOL_NAMES or not isinstance(query, dict):
        raise ValueError("evidence request must name an allowlisted query tool and object query")
    # Apply the same argument allowlist as Evidence Agent, with a synthetic
    # query-choice wrapper so no arbitrary tool argument can reach execution.
    _validate_tool_choice({"tool": tool, "query": query, "rationale": reason}, [])
    if item.get("status", "open") != "open":
        raise ValueError("new evidence requests must have status 'open'")
    return {"question": question.strip(), "requested_tool": tool, "query": query, "reason": reason.strip(), "status": "open"}


def _validate_diagnosis(output: Any, entries: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    hypotheses = output.get("hypotheses")
    requests = output.get("evidence_requests")
    if not isinstance(hypotheses, list) or len(hypotheses) > 5 or not isinstance(requests, list) or len(requests) > 3:
        raise ValueError("hypotheses and evidence_requests must be bounded arrays")
    observations = {str(entry.get("entry_id")): entry for entry in entries if _kind(entry) == "observation"}
    own_hypotheses = {str(entry.get("entry_id")): entry for entry in entries if _kind(entry) == "hypothesis" and entry.get("source_agent") == actor}
    clean_hypotheses = []
    for item in hypotheses:
        if not isinstance(item, dict):
            raise ValueError("each hypothesis must be an object")
        claim = item.get("claim")
        status = item.get("status")
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 700 or status not in _HYPOTHESIS_STATES:
            raise ValueError("hypothesis needs a short claim and permitted status")
        supporting = item.get("supporting_evidence_ids", [])
        contradicting = item.get("contradicting_evidence_ids", [])
        questions = item.get("open_questions", [])
        if not isinstance(supporting, list) or not isinstance(contradicting, list) or not isinstance(questions, list):
            raise ValueError("hypothesis references and open_questions must be arrays")
        if any(str(ref) not in observations for ref in supporting + contradicting):
            raise ValueError("hypothesis references an unknown or non-observation entry ID")
        if any(not isinstance(question, str) or not question.strip() for question in questions):
            raise ValueError("hypothesis open questions must be non-empty strings")
        if status == "supported":
            cited = [observations[str(ref)] for ref in supporting]
            if not cited or not _has_grounding(claim, cited):
                raise ValueError("supported hypothesis requires non-empty, claim-relevant, cause-specific observation records; generic latency, error rates and HTTP 502/upstream errors alone do not identify a root cause")
        revise_id = item.get("revise_entry_id")
        if revise_id is not None:
            prior = own_hypotheses.get(str(revise_id))
            if prior is None:
                raise ValueError("revise_entry_id must identify a current hypothesis owned by this agent")
        clean_hypotheses.append({
            "claim": claim.strip(), "status": status,
            "supporting_evidence_ids": [str(ref) for ref in supporting],
            "contradicting_evidence_ids": [str(ref) for ref in contradicting],
            "open_questions": [q.strip() for q in questions],
            "revise_entry_id": str(revise_id) if revise_id is not None else None,
        })
    return {"hypotheses": clean_hypotheses, "evidence_requests": [_validate_request(item) for item in requests]}


def _open_requests(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = {}
    for entry in entries:
        if _kind(entry) == "observation":
            data = _data(entry)
            req_id = data.get("fulfilled_request_entry_id")
            if req_id:
                completed[str(req_id)] = data.get("fulfilled_request_status")
    result = []
    for entry in entries:
        if _kind(entry) != "evidence_request":
            continue
        data = _data(entry)
        if str(data.get("status", "open")).lower() not in {"open", "pending"}:
            continue
        if already_observed(data.get("requested_tool"), data.get("query"), entries):
            continue
        if str(entry.get("entry_id")) in completed:
            continue
        result.append({"entry_id": str(entry.get("entry_id")), **data})
    return result


def _query_signature(tool: Any, query: Any) -> tuple[str, str] | None:
    """Return a stable signature for one fixture tool query."""
    if not isinstance(tool, str) or tool not in _FIXTURE_TOOL_NAMES or not isinstance(query, Mapping):
        return None
    return signature(tool, dict(query))


def _unavailable_queries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find exact tool queries already proven unavailable on this fixture case."""
    requests = {
        str(entry.get("entry_id")): entry
        for entry in entries
        if _kind(entry) == "evidence_request"
    }
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if _kind(entry) != "observation":
            continue
        data = _data(entry)
        if str(data.get("fulfilled_request_status", "")).lower() != "unavailable":
            continue
        request = requests.get(str(data.get("fulfilled_request_entry_id")))
        if request is not None:
            request_data = _data(request)
            tool, query = request_data.get("requested_tool"), request_data.get("query")
        else:
            tool, query = data.get("tool_name"), data.get("query")
        signature = _query_signature(tool, query)
        if signature is not None:
            found[signature] = {"tool": signature[0], "query": dict(query)}
    return list(found.values())


def _was_unavailable(tool: Any, query: Any, unavailable: list[dict[str, Any]]) -> bool:
    signature = _query_signature(tool, query)
    return signature is not None and any(
        signature == _query_signature(item.get("tool"), item.get("query"))
        for item in unavailable
    )


def _latest_reviews(entries: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    result: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for entry in entries:
        if _kind(entry) != "review":
            continue
        data = _data(entry)
        target_id, target_revision = data.get("target_entry_id"), data.get("target_revision")
        if target_id is not None and target_revision is not None:
            try:
                result.setdefault((str(target_id), int(target_revision)), []).append(entry)
            except (ValueError, TypeError):
                continue
    return result


def _validate_review(output: Any, target: dict[str, Any], entries: list[dict[str, Any]], purpose: str, retrieved_ids: set[str]) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ValueError("output must be a JSON object")
    if output.get("target_entry_id") != target.get("entry_id") or output.get("target_revision") != target.get("revision"):
        raise ValueError("review must target the exact entry revision supplied")
    verdict = output.get("verdict")
    if verdict not in _REVIEW_VERDICTS:
        raise ValueError("verdict must be accept, reject, or request_evidence")
    rationale = output.get("rationale")
    citations = output.get("supporting_evidence_ids", [])
    if not isinstance(rationale, str) or not rationale.strip() or not isinstance(citations, list):
        raise ValueError("review needs a rationale and evidence ID array")
    observation_map = {str(entry.get("entry_id")): entry for entry in entries if _kind(entry) == "observation"}
    if any(str(ref) not in observation_map for ref in citations):
        raise ValueError("review cites an unknown or non-observation entry")
    data = _data(target)
    if verdict == "accept":
        if purpose == "review_hypotheses":
            if data.get("status") != "supported":
                raise ValueError("only a supported hypothesis can be accepted")
            hypothesis_refs = {str(ref) for ref in data.get("supporting_evidence_ids", [])}
            cited = [observation_map[str(ref)] for ref in citations if str(ref) in hypothesis_refs]
            if not cited or not _has_grounding(str(data.get("claim", "")), cited):
                raise ValueError("review cannot accept a claim without relevant, non-empty cited records")
        else:
            solution_evidence = {str(ref) for ref in data.get("evidence_ids", [])}
            if not data.get("actions") or not set(data.get("runbook_ids", [])).issubset(retrieved_ids) or not data.get("runbook_ids"):
                raise ValueError("response cannot be accepted without actions and retrieved runbook sections")
            cited = [observation_map[str(ref)] for ref in citations if str(ref) in solution_evidence]
            if not cited or any(not _observation_text(entry) for entry in cited):
                raise ValueError("response cannot be accepted without non-empty cited observation records")
    follow_up = output.get("required_follow_up")
    if verdict == "request_evidence":
        follow_up = _validate_request({**(follow_up or {}), "status": "open"})
    elif follow_up is not None:
        raise ValueError("required_follow_up must be null unless verdict requests evidence")
    return {
        "target_entry_id": target["entry_id"], "target_revision": int(target["revision"]),
        "verdict": verdict, "rationale": rationale.strip(),
        "supporting_evidence_ids": [str(ref) for ref in citations],
        "required_follow_up": follow_up,
    }


def _validate_solution(output: Any, entries: list[dict[str, Any]], expected_hypothesis_refs: list[dict[str, Any]], retrieved_ids: set[str]) -> dict[str, Any]:
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
        if not isinstance(action, dict) or any(not isinstance(action.get(key), str) or not action[key].strip() for key in ("step", "purpose", "expected_check")):
            raise ValueError("each action requires step, purpose, and expected_check text")
        clean_actions.append({key: action[key].strip() for key in ("step", "purpose", "expected_check")})
    if not isinstance(caveats, list) or any(not isinstance(item, str) or not item.strip() for item in caveats):
        raise ValueError("caveats must be an array of non-empty strings")
    observation_ids = {str(entry.get("entry_id")) for entry in entries if _kind(entry) == "observation"}
    evidence_ids = output.get("evidence_ids", [])
    runbook_ids = output.get("runbook_ids", [])
    if not isinstance(evidence_ids, list) or any(str(ref) not in observation_ids for ref in evidence_ids):
        raise ValueError("solution must cite existing observation entry IDs")
    if not isinstance(runbook_ids, list) or not runbook_ids or any(ref not in retrieved_ids for ref in runbook_ids):
        raise ValueError("solution must cite exact section IDs returned by runbook retrieval")
    cited_hypotheses = {
        str(ref) for hypothesis_ref in expected_hypothesis_refs
        for ref in [hypothesis_ref.get("entry_id")]
    }
    allowed_evidence = {
        str(ref)
        for entry in entries if _kind(entry) == "hypothesis" and str(entry.get("entry_id")) in cited_hypotheses
        for ref in _data(entry).get("supporting_evidence_ids", [])
    }
    if not evidence_ids or not set(map(str, evidence_ids)).issubset(allowed_evidence):
        raise ValueError("solution must cite non-empty evidence used by the accepted diagnosis")
    return {
        "summary": summary.strip(), "actions": clean_actions,
        "caveats": [item.strip() for item in caveats],
        "evidence_ids": [str(ref) for ref in evidence_ids],
        "runbook_ids": list(runbook_ids), "hypothesis_refs": expected_hypothesis_refs,
    }


class SpecialistRunner:
    """Run one bounded specialist task; all shared state uses Blackboard A2A calls."""

    def __init__(
        self,
        role: str,
        *,
        blackboard: Any,
        model: ModelAdapter,
        tools: ToolRegistry | None = None,
    ) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown specialist role: {role}")
        self.role = role
        self.blackboard = blackboard
        self.model = model
        self.chain = specialist_chain(model)
        self.tools = tools or ToolRegistry()
        self.actor = str(getattr(blackboard, "actor", role))
        self.last_run_usage: list[dict[str, Any]] = []

    async def _bb(self, action: str, **params: Any) -> dict[str, Any]:
        result = await self.blackboard.call(action, **params)
        if not isinstance(result, dict):
            raise SpecialistRunError(f"Blackboard {action} returned a non-object result")
        if _is_error(result):
            raise SpecialistRunError(f"Blackboard {action} failed: {result.get('error') or result}")
        return result

    async def _ask(self, purpose: str, context: dict[str, Any], validator: Callable[[Any], Any]) -> Any:
        instructions = _prompt(self.role)
        schema = context.pop("_output_schema")
        context["available_tools"] = {
            "query_metrics": {"metric_names": "optional list of exact metric names", "contains": "optional string or null"},
            "query_logs": {"contains": "optional string matching ALL words", "service": "optional exact service name", "level": "optional single lowercase debug/info/warning/error/critical or null", "limit": "optional integer 1..100"},
            "get_change_history": {"service": "optional exact service name", "limit": "optional integer 1..100"},
        }
        context["tool_query_rules"] = "Only listed argument keys are allowed. query_metrics does NOT accept service, limit, time ranges, or filters. There are no DNS lookup or infrastructure tools. An evidence request must use requested_tool and query, not tool or arguments. Request unavailable evidence using a valid query; do not invent a tool."
        attempts: list[dict[str, Any]] = []
        for attempt in range(2):
            request_context = context if attempt == 0 else {
                **context,
                "repair_feedback": {
                    "previous_output": attempts[-1]["raw"][:6000],
                    "validation_error": attempts[-1]["error"][:1500],
                    "instruction": "Return a corrected JSON object that satisfies the original schema and all source-reference rules.",
                },
            }
            try:
                raw = await self.chain.ainvoke({
                    "instructions": instructions,
                    "task": json.dumps({"role": self.role, "purpose": purpose,
                                        "context": request_context, "output_schema": schema}),
                }, config={"run_name": f"{self.role}:{purpose}",
                           "tags": ["specialist", self.role],
                           "metadata": {"role": self.role, "purpose": purpose, "attempt": attempt + 1}})
            finally:
                metadata = getattr(self.model, "last_call_metadata", None)
                if isinstance(metadata, Mapping):
                    self.last_run_usage.append(dict(metadata))
            if not isinstance(raw, str):
                raise SpecialistRunError("model adapter returned non-text output")
            try:
                decoded = json.loads(raw)
                return validator(decoded)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                attempts.append({"raw": raw, "error": str(exc)})
                if attempt == 1:
                    raise SpecialistRunError(f"model output invalid after one repair attempt: {exc}") from exc
        raise AssertionError("unreachable")

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SpecialistRunError("task payload must be an object")
        problem_id = payload.get("problem_id")
        purpose = payload.get("purpose")
        dispatch_id = payload.get("dispatch_id")
        if not all(isinstance(value, str) and value.strip() for value in (problem_id, purpose, dispatch_id)):
            raise SpecialistRunError("task requires problem_id, purpose, and dispatch_id")
        if purpose not in PURPOSES[self.role]:
            raise SpecialistRunError(f"purpose {purpose!r} is not permitted for role {self.role!r}")
        self.last_run_usage = []
        snapshot = await self._bb("read_blackboard", problem_id=problem_id)
        problem = snapshot.get("problem")
        if not isinstance(problem, Mapping) or problem.get("problem_id") != problem_id:
            raise SpecialistRunError("Blackboard snapshot does not contain the requested problem")
        case_id = problem.get("case_id")
        if not isinstance(case_id, str):
            raise SpecialistRunError("Blackboard problem has no fixture case_id")
        if purpose == "collect_evidence":
            replay = self._completed_evidence(snapshot, problem_id, case_id, dispatch_id)
            if replay is not None:
                return replay
        summary = read_case_summary(case_id)
        summary["query_catalog"] = read_query_catalog(case_id)
        entries = _entries(snapshot)

        if purpose == "collect_evidence":
            return await self._collect(problem_id, case_id, dispatch_id, summary, entries)
        elif purpose == "diagnose":
            result, summary_text = await self._diagnose(problem_id, dispatch_id, summary, entries)
        elif purpose in {"review_hypotheses", "review_solution"}:
            result, summary_text = await self._review(problem_id, purpose, dispatch_id, summary, entries)
        else:
            result, summary_text = await self._respond(problem_id, dispatch_id, summary, entries)
        return {
            "ok": True,
            "role": self.role,
            "problem_id": problem_id,
            "purpose": purpose,
            "dispatch_id": dispatch_id,
            "entry_ids": result,
            "summary": summary_text,
            "model_mode": getattr(self.model, "mode", "unknown"),
            "model_usage": list(self.last_run_usage),
        }

    def _completed_evidence(self, snapshot: Mapping[str, Any], problem_id: str, case_id: str, dispatch_id: str) -> dict[str, Any] | None:
        # History retains the committed original even if an observation was later revised.
        candidates = snapshot.get("history", []) or snapshot.get("entries", [])
        for entry in candidates:
            if not isinstance(entry, Mapping) or _kind(entry) != "observation" or entry.get("source_agent") != self.actor or entry.get("problem_id") != problem_id:
                continue
            data = _data(entry)
            result = data.get("dispatch_result")
            if not isinstance(result, Mapping) or any(result.get(key) != value for key, value in {
                "dispatch_id": dispatch_id, "role": self.role, "purpose": "collect_evidence", "case_id": case_id,
            }.items()):
                continue
            state = "returned records" if data.get("records") else "returned no matching records"
            return {
                "ok": True, "role": self.role, "problem_id": problem_id, "purpose": "collect_evidence",
                "dispatch_id": dispatch_id, "entry_ids": [str(entry["entry_id"])],
                "summary": f"{data['tool_name']} {state}; recorded tool provenance and request linkage.",
                "model_mode": result["model_mode"], "model_usage": result["model_usage"],
            }
        return None

    async def _collect(self, problem_id: str, case_id: str, dispatch_id: str, summary: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
        open_requests = _open_requests(entries)
        context = {
            "problem": summary,
            "open_evidence_requests": open_requests,
            "available_tools": {
                "query_metrics": {"arguments": {"metric_names": "list of metric names", "contains": "optional string"}},
                "query_logs": {"arguments": {"contains": "substring filter", "service": "optional service filter", "level": "optional severity", "limit": "optional 1-100"}},
                "get_change_history": {"arguments": {"service": "optional service filter", "limit": "optional 1-100"}},
            },
            "_output_schema": {"tool": "allowlisted tool name", "query": "object of that tool's permitted arguments", "rationale": "string", "fulfilled_request_entry_id": "string or null"},
        }
        if not open_requests and not any(_kind(e) == "observation" for e in entries) and getattr(self.model, "mode", "") == "live":
            # One bounded baseline, published atomically with provenance. No model
            # guesses are needed to discover which records exist.
            components = [self.tools.execute(tool, case_id=case_id, arguments={})
                          for tool in ("query_metrics", "query_logs", "get_change_history")]
            choice = {"rationale": "Collect bounded baseline metrics, recent logs, and changes before diagnosis.", "fulfilled_request_entry_id": None}
            tool_result = {
                "tool_name": "baseline", "query": {}, "components": components,
                "records": [r for item in components for r in item["records"]],
                "source_ids": [r for item in components for r in item["source_ids"]],
                "observed_at": components[0]["observed_at"],
            }
        else:
            if open_requests and getattr(self.model, "mode", "") == "live":
                # An approved exact query is execution work, not another model
                # decision. Preserve the request rather than asking an LLM to copy it.
                reviewer_ids = {str(e.get("entry_id")) for e in entries if e.get("source_agent") == "review"}
                selected = next((r for r in open_requests if r["entry_id"] in reviewer_ids), open_requests[0])
                query = dict(selected["query"])
                if isinstance(query.get("limit"), float) and query["limit"].is_integer():
                    query["limit"] = int(query["limit"])
                choice = _validate_tool_choice({"tool": selected["requested_tool"], "query": query,
                    "rationale": selected["reason"], "fulfilled_request_entry_id": selected["entry_id"]}, open_requests)
            else:
                choice = await self._ask("collect_evidence", context, lambda output: _validate_tool_choice(output, open_requests))
            tool_result = self.tools.execute(choice["tool"], case_id=case_id, arguments=choice["query"])
        if not isinstance(tool_result, dict) or not isinstance(tool_result.get("records"), list):
            raise SpecialistRunError("fixture tool returned an invalid result object")
        request_id = choice["fulfilled_request_entry_id"]
        data = {
            "tool_name": tool_result["tool_name"],
            "query": tool_result["query"],
            "source_ids": tool_result["source_ids"],
            "observed_at": tool_result["observed_at"],
            "records": tool_result["records"],
            **({"components": tool_result["components"]} if "components" in tool_result else {}),
            "rationale": choice["rationale"],
            "fulfilled_request_entry_id": request_id,
            "fulfilled_request_status": ("fulfilled" if tool_result["records"] else "unavailable") if request_id else None,
            "dispatch_result": {
                "dispatch_id": dispatch_id, "role": self.role, "purpose": "collect_evidence", "case_id": case_id,
                "model_mode": getattr(self.model, "mode", "unknown"),
                "model_usage": list(self.last_run_usage),
            },
        }
        try:
            response = await self._bb(
                "write_blackboard", problem_id=problem_id, knowledge_type="observation", data=data,
                request_id=_deterministic_id(dispatch_id, "observation"),
            )
        except Exception:
            # Another delivery may have committed after our initial read, or
            # the write acknowledgement may have been lost after commit.
            snapshot = await self._bb("read_blackboard", problem_id=problem_id)
            replay = self._completed_evidence(snapshot, problem_id, case_id, dispatch_id)
            if replay is not None:
                return replay
            raise
        entry_id = _result_entry_id(response, f"dispatch:{dispatch_id}:observation")
        completed = self._completed_evidence({"entries": [{
            "entry_id": entry_id, "problem_id": problem_id, "knowledge_type": "observation",
            "source_agent": self.actor, "data": data,
        }]}, problem_id, case_id, dispatch_id)
        assert completed is not None
        return completed

    async def _diagnose(self, problem_id: str, dispatch_id: str, summary: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[list[str], str]:
        observations = [entry for entry in entries if _kind(entry) == "observation"]
        own = [entry for entry in entries if _kind(entry) == "hypothesis" and entry.get("source_agent") == self.actor]
        context = {
            "problem": summary,
            "observations": observations,
            "own_hypotheses": own,
            "open_evidence_requests": _open_requests(entries),
            "unavailable_evidence_queries": _unavailable_queries(entries),
            "allowed_tools": [
                "query_metrics(metric_names, contains)", "query_logs(contains, service, level, limit)", "get_change_history(service, limit)"
            ],
            "_output_schema": {
                "hypotheses": [{"claim": "string", "status": sorted(_HYPOTHESIS_STATES), "supporting_evidence_ids": [], "contradicting_evidence_ids": [], "open_questions": [], "revise_entry_id": "optional current own hypothesis entry ID"}],
                "evidence_requests": [{"question": "string", "requested_tool": sorted(_FIXTURE_TOOL_NAMES), "query": "object", "reason": "string", "status": "open"}],
            },
        }
        decision = await self._ask("diagnose", context, lambda output: _validate_diagnosis(output, entries, self.actor))
        written: list[str] = []
        suppressed_unavailable_request = False
        for index, hypothesis in enumerate(decision["hypotheses"]):
            data = {key: hypothesis[key] for key in ("claim", "status", "supporting_evidence_ids", "contradicting_evidence_ids", "open_questions")}
            data["observation_refs"] = [_ref(entry) for entry in sorted(observations, key=lambda item: str(item.get("entry_id")))]
            revise_id = hypothesis.get("revise_entry_id")
            if revise_id:
                prior = next(entry for entry in own if str(entry.get("entry_id")) == revise_id)
                if _data(prior) == data:
                    # Replaying unchanged analysis must not manufacture a new
                    # revision that invalidates an otherwise current review.
                    entry_id = revise_id
                else:
                    response = await self._bb(
                        "update_knowledge", problem_id=problem_id,
                        entry_id=revise_id, expected_revision=int(prior.get("revision", 1)),
                        knowledge_type="hypothesis", data=data,
                        request_id=_deterministic_id(dispatch_id, f"hypothesis:{index}:{revise_id}"),
                    )
                    entry_id = _result_entry_id(response, revise_id)
            else:
                response = await self._bb(
                    "write_blackboard", problem_id=problem_id,
                    knowledge_type="hypothesis", data=data,
                    request_id=_deterministic_id(dispatch_id, f"hypothesis:{index}"),
                )
                entry_id = _result_entry_id(response, f"dispatch:{dispatch_id}:hypothesis:{index}")
            written.append(entry_id)
        for index, request in enumerate(decision["evidence_requests"]):
            if already_observed(request.get("requested_tool"), request.get("query"), entries):
                continue
            if _was_unavailable(request.get("requested_tool"), request.get("query"), context["unavailable_evidence_queries"]):
                suppressed_unavailable_request = True
                continue
            existing_request = next((entry for entry in entries if _same_request(request, entry) and _kind(entry) == "evidence_request" and str(_data(entry).get("status", "open")).lower() in {"open", "pending"}), None)
            if existing_request is not None:
                written.append(str(existing_request.get("entry_id")))
                continue
            response = await self._bb(
                "write_blackboard", problem_id=problem_id,
                knowledge_type="evidence_request", data=request,
                request_id=_deterministic_id(dispatch_id, f"evidence_request:{index}"),
            )
            written.append(_result_entry_id(response, f"dispatch:{dispatch_id}:evidence_request:{index}"))
        if not written and suppressed_unavailable_request:
            # All proposed follow-ups were already attempted without records.
            # Keep an honest unresolved finding on the board so the controller
            # can converge instead of treating the repeated request as a crash.
            prior = next((entry for entry in own if _data(entry).get("status") == "unresolved"), None)
            if prior is not None:
                refreshed = {**_data(prior), "observation_refs": [_ref(entry) for entry in sorted(observations, key=lambda item: str(item.get("entry_id")))]}
                if refreshed != _data(prior):
                    await self._bb(
                        "update_knowledge", problem_id=problem_id, entry_id=str(prior["entry_id"]),
                        expected_revision=int(prior.get("revision", 1)), data=refreshed,
                        request_id=_deterministic_id(dispatch_id, "refresh_unresolved_observation_refs"),
                    )
                written.append(str(prior.get("entry_id")))
            else:
                unresolved = {
                    "observation_refs": [_ref(entry) for entry in sorted(observations, key=lambda item: str(item.get("entry_id")))],
                    "claim": "The available fixture evidence remains insufficient to identify a root cause",
                    "status": "unresolved",
                    "supporting_evidence_ids": [
                        str(entry.get("entry_id")) for entry in observations if _data(entry).get("records")
                    ],
                    "contradicting_evidence_ids": [],
                    "open_questions": [],
                }
                response = await self._bb(
                    "write_blackboard", problem_id=problem_id,
                    knowledge_type="hypothesis", data=unresolved,
                    request_id=_deterministic_id(dispatch_id, "unresolved_after_unavailable_query"),
                )
                written.append(_result_entry_id(response, f"dispatch:{dispatch_id}:unresolved"))
        if not written:
            raise SpecialistRunError("diagnosis produced no hypotheses or new evidence requests")
        return written, f"Processed {len(decision['hypotheses'])} hypothesis result(s); {len(decision['evidence_requests'])} follow-up request(s) were proposed before unavailable-query filtering."

    async def _review(self, problem_id: str, purpose: str, dispatch_id: str, summary: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[list[str], str]:
        target = self._select_review_target(entries, purpose)
        if target is None:
            raise SpecialistRunError(f"no current entry is eligible for {purpose}")
        observations = [entry for entry in entries if _kind(entry) == "observation"]
        retrieved: list[dict[str, Any]] = []
        if purpose == "review_solution":
            solution_data = _data(target)
            query = f"{solution_data.get('summary', '')} " + " ".join(
                str(action.get("step", "")) for action in solution_data.get("actions", []) if isinstance(action, Mapping)
            )
            result = self.tools.execute("search_runbooks", arguments={"query": query[:500], "limit": 10})
            retrieved = result.get("results", [])
        retrieved_ids = {str(item.get("section_id")) for item in retrieved}
        context = {
            "problem": summary,
            "purpose": purpose,
            "target": target,
            "observations": observations,
            "observation_entry_ids": [entry.get("entry_id") for entry in observations],
            "open_evidence_requests": _open_requests(entries),
            "unavailable_evidence_queries": _unavailable_queries(entries),
            "retrieved_runbooks": retrieved,
            "retrieved_runbook_ids": sorted(retrieved_ids),
            "_output_schema": {
                "target_entry_id": "exact target entry ID", "target_revision": "exact target revision",
                "verdict": sorted(_REVIEW_VERDICTS), "rationale": "string",
                "supporting_evidence_ids": [], "required_follow_up": {"question": "nonempty string", "requested_tool": sorted(_FIXTURE_TOOL_NAMES), "query": "object containing ONLY arguments listed in available_tools for the selected tool", "reason": "nonempty string"},
            },
        }
        review = await self._ask(purpose, context, lambda output: _validate_review(output, target, entries, purpose, retrieved_ids))
        follow_up = review.get("required_follow_up")
        if (
            review["verdict"] == "request_evidence"
            and isinstance(follow_up, Mapping)
            and _was_unavailable(
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
        review_data = {key: review[key] for key in ("target_entry_id", "target_revision", "verdict", "rationale", "supporting_evidence_ids", "required_follow_up")}
        response = await self._bb(
            "write_blackboard", problem_id=problem_id,
            knowledge_type="review", data=review_data,
            request_id=_deterministic_id(dispatch_id, "review"),
        )
        written = [_result_entry_id(response, f"dispatch:{dispatch_id}:review")]
        follow_up = review.get("required_follow_up")
        if review["verdict"] == "request_evidence" and follow_up:
            active = [entry for entry in entries if _kind(entry) == "evidence_request" and str(_data(entry).get("status", "open")).lower() in {"open", "pending"}]
            if not any(_same_request(follow_up, entry) for entry in active):
                request_response = await self._bb(
                    "write_blackboard", problem_id=problem_id,
                    knowledge_type="evidence_request", data=follow_up,
                    request_id=_deterministic_id(dispatch_id, "review_evidence_request"),
                )
                written.append(_result_entry_id(request_response, f"dispatch:{dispatch_id}:evidence_request"))
            else:
                existing = next(entry for entry in active if _same_request(follow_up, entry))
                written.append(str(existing.get("entry_id")))
        return written, f"{review['verdict']} review for {target['entry_id']} revision {target['revision']}."

    def _select_review_target(self, entries: list[dict[str, Any]], purpose: str) -> dict[str, Any] | None:
        reviewed = _latest_reviews(entries)
        if purpose == "review_hypotheses":
            current_observations = {
                (str(entry.get("entry_id")), int(entry.get("revision", 1)))
                for entry in entries if _kind(entry) == "observation"
            }
            def current_context(entry: Mapping[str, Any]) -> bool:
                refs = _data(entry).get("observation_refs")
                if not isinstance(refs, list):
                    return False
                try:
                    versions = {(str(ref["entry_id"]), int(ref["revision"])) for ref in refs}
                except (KeyError, TypeError, ValueError):
                    return False
                return versions == current_observations
            candidates = [
                entry for entry in entries if _kind(entry) == "hypothesis"
                and current_context(entry)
                and (str(entry.get("entry_id")), int(entry.get("revision", 1))) not in reviewed
            ]
        else:
            candidates = [entry for entry in entries if _kind(entry) == "solution" and (str(entry.get("entry_id")), int(entry.get("revision", 1))) not in reviewed]
            actor_solutions = [entry for entry in candidates if entry.get("source_agent") == self.actor]
            if actor_solutions:
                candidates = actor_solutions
        return sorted(candidates, key=lambda entry: (_data(entry).get("status") != "supported", str(entry.get("entry_id"))))[0] if candidates else None

    async def _respond(self, problem_id: str, dispatch_id: str, summary: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[list[str], str]:
        reviews = _latest_reviews(entries)
        viable = []
        for entry in entries:
            if _kind(entry) != "hypothesis" or _data(entry).get("status") != "supported":
                continue
            ref = (str(entry.get("entry_id")), int(entry.get("revision", 1)))
            accepted = any(_data(review).get("verdict") == "accept" for review in reviews.get(ref, []))
            if accepted:
                viable.append(entry)
        if not viable:
            raise SpecialistRunError("response task requires a currently accepted supported hypothesis")
        refs = [_ref(entry) for entry in sorted(viable, key=lambda item: str(item.get("entry_id")))]
        claims = [str(_data(entry).get("claim", "")) for entry in viable]
        runbook_result = self.tools.execute("search_runbooks", arguments={"query": " ".join(claims)[:500], "limit": 10})
        runbooks = runbook_result.get("results", [])
        runbook_ids = {str(item.get("section_id")) for item in runbooks}
        if not runbooks:
            raise SpecialistRunError("no local runbook sections matched the accepted diagnosis")
        observations = [entry for entry in entries if _kind(entry) == "observation"]
        hypothesis_observation_ids = {
            str(ref) for hypothesis in viable
            for ref in _data(hypothesis).get("supporting_evidence_ids", [])
        }
        context = {
            "problem": summary,
            "supported_hypotheses": viable,
            "hypothesis_refs": refs,
            "observations": [entry for entry in observations if str(entry.get("entry_id")) in hypothesis_observation_ids],
            "runbooks": runbooks,
            "_output_schema": {
                "summary": "string", "actions": [{"step": "string", "purpose": "string", "expected_check": "string"}],
                "caveats": [], "evidence_ids": "existing cited observation entry IDs",
                "runbook_ids": "exact retrieved section IDs", "hypothesis_refs": refs,
            },
        }
        solution = await self._ask("propose_response", context, lambda output: _validate_solution(output, entries, refs, runbook_ids))
        own_solutions = [entry for entry in entries if _kind(entry) == "solution" and entry.get("source_agent") == self.actor]
        latest_own = sorted(own_solutions, key=lambda item: int(item.get("revision", 1)))[-1] if own_solutions else None
        if latest_own:
            response = await self._bb(
                "update_knowledge", problem_id=problem_id,
                entry_id=latest_own["entry_id"], expected_revision=int(latest_own.get("revision", 1)),
                knowledge_type="solution", data=solution,
                request_id=_deterministic_id(dispatch_id, "solution"),
            )
            entry_id = _result_entry_id(response, str(latest_own["entry_id"]))
        else:
            response = await self._bb(
                "write_blackboard", problem_id=problem_id,
                knowledge_type="solution", data=solution,
                request_id=_deterministic_id(dispatch_id, "solution"),
            )
            entry_id = _result_entry_id(response, f"dispatch:{dispatch_id}:solution")
        return [entry_id], f"Recorded a runbook-backed proposal with {len(solution['actions'])} ordered actions."


def _same_request(request: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    data = _data(entry)
    return (
        request.get("requested_tool") == data.get("requested_tool")
        and request.get("query") == data.get("query")
        and request.get("question") == data.get("question")
    )


def build_specialist_handler(
    role: str,
    settings: Any | None = None,
    *,
    blackboard: Any | None = None,
    model: ModelAdapter | None = None,
    tools: ToolRegistry | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Create the async A2A handler expected by `create_a2a_app`.

    The optional injections make tests deterministic. Normal service startup
    creates a per-task model adapter and runner from Settings. An explicitly
    injected adapter is shared only under a handler lock.
    """
    if role not in ROLES:
        raise ValueError(f"unknown specialist role: {role}")
    if settings is None:
        from ..config import get_settings
        settings = get_settings()
    if blackboard is None:
        from ..blackboard.client import BlackboardClient
        urls = getattr(settings, "agent_urls", {})
        url = (
            getattr(settings, "blackboard_url", None)
            or (urls.get("blackboard") if isinstance(urls, Mapping) else None)
            or "http://127.0.0.1:8100"
        )
        credentials = getattr(settings, "agent_credentials", {})
        actor = role
        token = credentials.get(actor) if isinstance(credentials, Mapping) else None
        blackboard = BlackboardClient(base_url=url, actor=actor, api_key=token, timeout=float(getattr(settings, "request_timeout", 30.0)))
    if model is None:
        async def handle(payload: dict[str, Any]) -> dict[str, Any]:
            runner = SpecialistRunner(
                role,
                blackboard=blackboard,
                model=model_from_settings(settings),
                tools=tools,
            )
            return await runner.run(payload)

        return handle

    runner = SpecialistRunner(role, blackboard=blackboard, model=model, tools=tools)
    shared_adapter_lock = asyncio.Lock()

    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        async with shared_adapter_lock:
            return await runner.run(payload)

    return handle
