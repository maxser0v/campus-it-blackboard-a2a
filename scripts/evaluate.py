"""Run fixture cases and write measured, case-by-case evaluation JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from campus_blackboard.blackboard.client import BlackboardClient
from campus_blackboard.config import get_settings
from campus_blackboard.tools.fixtures import FixtureError, validate_fixture_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANSWER_KEY_ROOT = PROJECT_ROOT / "evaluation" / "answer_keys"
RESULT_ROOT = PROJECT_ROOT / "evaluation" / "results"


def _latest_review(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not reviews:
        return None
    return sorted(reviews, key=lambda e: (str(e.get("created_at", "")), str(e.get("entry_id", ""))))[-1]


def _data(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("data", {})
    return value if isinstance(value, dict) else {}


def _queries_match(tool: Any, first: Any, second: Any) -> bool:
    """Compare validated arguments with the fixture tools' explicit defaults."""
    if not isinstance(tool, str) or not isinstance(first, dict) or not isinstance(second, dict):
        return False
    # A2A Struct represents numeric fields as floats on the wire. Restore
    # integral limits before validating query meaning; reject fractional limits.
    first, second = dict(first), dict(second)
    for query in (first, second):
        if isinstance(query.get("limit"), float) and query["limit"].is_integer():
            query["limit"] = int(query["limit"])
    try:
        validate_fixture_arguments(tool, first)
        validate_fixture_arguments(tool, second)
    except FixtureError:
        return False
    def canonical(query: dict[str, Any]) -> dict[str, Any]:
        if tool == "query_metrics":
            return {"metric_names": sorted(set(query.get("metric_names") or [])), "contains": query.get("contains")}
        defaults = {"service": None, "limit": 20}
        if tool == "query_logs":
            defaults.update(contains=None, level=None)
        return defaults | query
    return canonical(first) == canonical(second)


def assess(
    case_id: str,
    snapshot: dict[str, Any],
    run: dict[str, Any],
    answer_key: dict[str, Any],
    *,
    configured_max_dispatches: int = 12,
) -> dict[str, Any]:
    entries = snapshot.get("entries", [])
    observations = [e for e in entries if e.get("knowledge_type") == "observation"]
    hypotheses = [e for e in entries if e.get("knowledge_type") == "hypothesis"]
    reviews = [e for e in entries if e.get("knowledge_type") == "review"]
    solutions = [e for e in entries if e.get("knowledge_type") == "solution"]
    decisions = [e for e in entries if e.get("knowledge_type") == "controller_decision"]
    observation_ids = {str(e.get("entry_id")) for e in observations}
    review_index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for review in reviews:
        data = _data(review)
        try:
            key = (str(data["target_entry_id"]), int(data["target_revision"]))
        except (KeyError, TypeError, ValueError):
            continue
        review_index.setdefault(key, []).append(review)

    supported_accepts = []
    all_hypothesis_refs_valid = True
    for hypothesis in hypotheses:
        data = _data(hypothesis)
        refs = list(data.get("supporting_evidence_ids", [])) + list(data.get("contradicting_evidence_ids", []))
        all_hypothesis_refs_valid = all_hypothesis_refs_valid and all(str(ref) in observation_ids for ref in refs)
        matched = review_index.get((str(hypothesis.get("entry_id")), int(hypothesis.get("revision", 1))), [])
        latest = _latest_review(matched)
        if data.get("status") == "supported" and latest and _data(latest).get("verdict") == "accept":
            supported_accepts.append(hypothesis)

    latest_solution = sorted(
        solutions,
        key=lambda e: (str(e.get("created_at", "")), int(e.get("revision", 1)), str(e.get("entry_id", ""))),
    )[-1] if solutions else None
    solution_data = _data(latest_solution) if latest_solution else {}
    solution_reviews = (
        review_index.get((str(latest_solution.get("entry_id")), int(latest_solution.get("revision", 1))), [])
        if latest_solution
        else []
    )
    latest_solution_review = _latest_review(solution_reviews)
    solution_reviewed_accept = bool(latest_solution_review and _data(latest_solution_review).get("verdict") == "accept")
    solution_evidence_ids = list(solution_data.get("evidence_ids", []))
    solution_refs_valid = bool(solution_evidence_ids) and all(str(ref) in observation_ids for ref in solution_evidence_ids)
    dispatches = [
        _data(e)
        for e in decisions
        if _data(e).get("event_type") == "dispatch_started"
    ]
    requests = [e for e in entries if e.get("knowledge_type") == "evidence_request"]
    problem = snapshot.get("problem", {})
    limits = problem.get("limits", {}) if isinstance(problem, dict) else {}
    persisted_dispatch_limit = limits.get("max_dispatches") if isinstance(limits, dict) else None
    dispatch_limit = (
        persisted_dispatch_limit
        if isinstance(persisted_dispatch_limit, int) and not isinstance(persisted_dispatch_limit, bool) and persisted_dispatch_limit > 0
        else configured_max_dispatches
    )
    fulfilled_request_ids = {
        str(_data(e).get("fulfilled_request_entry_id"))
        for e in observations
        if _data(e).get("fulfilled_request_entry_id")
        and _data(e).get("fulfilled_request_status") == "fulfilled"
    }
    expected_status = {
        "db_pool": {"completed"},
        "dns_fault": {"completed"},
        "missing_evidence": {"unresolved"},
    }.get(case_id, set())
    actual_status = str(run.get("status", "unknown"))
    mentioned_ground_truth = any(
        str(answer_key.get("ground_truth", "")).casefold() in str(_data(e).get("claim", "")).casefold()
        for e in hypotheses
    )

    checks = {
        "terminal_status_matches_case_expectation": actual_status in expected_status,
        "hypothesis_evidence_references_resolve": all_hypothesis_refs_valid,
        "current_supported_hypothesis_is_accepted": bool(supported_accepts),
        "response_reviewed_at_current_revision": solution_reviewed_accept,
        "response_evidence_references_resolve": solution_refs_valid,
        "response_has_runbook_reference": bool(solution_data.get("runbook_ids")),
        "requested_evidence_was_linked": bool(fulfilled_request_ids),
        "dispatch_limit_respected": len(dispatches) <= dispatch_limit,
        "ground_truth_label_appears_in_hypothesis": mentioned_ground_truth,
    }
    if case_id == "missing_evidence":
        request_by_id = {str(e.get("entry_id")): _data(e) for e in requests}
        unavailable_request_observed = any(
            _data(observation).get("fulfilled_request_status") == "unavailable"
            and _data(observation).get("records") == []
            and str(_data(observation).get("fulfilled_request_entry_id")) in request_by_id
            and _data(observation).get("tool_name")
            == request_by_id[str(_data(observation).get("fulfilled_request_entry_id"))].get("requested_tool")
            and _queries_match(
                _data(observation).get("tool_name"), _data(observation).get("query"),
                request_by_id[str(_data(observation).get("fulfilled_request_entry_id"))].get("query"),
            )
            for observation in observations
        )
        evidence_stop_reason = (run.get("result") or {}).get("reason") in {
            "hypotheses_rejected_without_new_evidence",
            "no_supported_reviewed_hypothesis",
            "evidence_request_not_satisfied",
            "no_eligible_action",
            "repeated_queries_returned_no_evidence",
        }
        checks["missing_evidence_unavailable_request_recorded"] = unavailable_request_observed
        checks["missing_evidence_stopped_for_evidence_reason"] = evidence_stop_reason
        checks["missing_evidence_ends_without_accepted_cause"] = (
            actual_status == "unresolved"
            and evidence_stop_reason
            and unavailable_request_observed
            and not supported_accepts
            and not solutions
        )
    return {
        "case_id": case_id,
        "problem_id": run.get("problem_id"),
        "expected_ground_truth_label": answer_key.get("ground_truth"),
        "status": actual_status,
        "operational_failure": actual_status == "blocked",
        "reason": (run.get("result") or {}).get("reason"),
        "elapsed_seconds": round(
            max(0.0, _iso_delta((run.get("result") or {}).get("started_at"), (run.get("result") or {}).get("finished_at"))),
            3,
        ),
        "dispatch_count": len(dispatches),
        "entry_counts": {
            "observations": len(observations),
            "hypotheses": len(hypotheses),
            "evidence_requests": len(requests),
            "reviews": len(reviews),
            "solutions": len(solutions),
        },
        "checks": checks,
        "manual_review_required": True,
        "note": "Label matching is a rough signal; inspect each cited record and claim manually.",
    }


def _iso_delta(start: str | None, finish: str | None) -> float:
    if not start or not finish:
        return 0.0
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(finish.replace("Z", "+00:00"))
        return (last - first).total_seconds()
    except ValueError:
        return 0.0


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    if settings.model_mode == "live" and not args.allow_live:
        raise RuntimeError("Live mode is configured. Pass --allow-live only when a live evaluation call is intended.")
    controller_url = f"http://{settings.bind_host}:{settings.controller_port}"
    dashboard_board = BlackboardClient(
        f"http://{settings.bind_host}:{settings.blackboard_port}",
        actor="dashboard",
        api_key=settings.agent_credentials.get("dashboard"),
        timeout=settings.request_timeout,
    )
    async with httpx.AsyncClient(timeout=10) as http:
        case_response = await http.get(f"{controller_url}/cases")
        case_response.raise_for_status()
        cases = {row["case_id"]: row for row in case_response.json()}
        chosen = args.cases or list(cases)
        unknown = sorted(set(chosen) - set(cases))
        if unknown:
            raise RuntimeError(f"Unknown case IDs: {', '.join(unknown)}")

        measurements = []
        for case_id in chosen:
            answer_key_path = ANSWER_KEY_ROOT / f"{case_id}.json"
            answer_key = json.loads(answer_key_path.read_text(encoding="utf-8"))
            for repetition in range(1, args.repeat + 1):
                run_info: dict[str, Any] = {}
                problem_id: str | None = None
                try:
                    start = await http.post(
                        f"{controller_url}/runs",
                        json={"case_id": case_id, "client_request_id": str(uuid.uuid4())},
                    )
                    start.raise_for_status()
                    run_info = start.json()
                    problem_id = str(run_info["problem_id"])
                    started_wait = time.monotonic()
                    while True:
                        await asyncio.sleep(args.poll_seconds)
                        current = None
                        for attempt in range(settings.max_retries + 1):
                            current = await http.get(f"{controller_url}/runs/{problem_id}")
                            if current.status_code == 503 and attempt < settings.max_retries:
                                await asyncio.sleep(0.2 * (attempt + 1))
                                continue
                            break
                        if current is None:
                            raise RuntimeError("Controller status request returned no response.")
                        current.raise_for_status()
                        run_info = current.json()
                        if run_info.get("status") in {"completed", "unresolved", "blocked", "cancelled"} and run_info.get("result") is not None:
                            break
                        if time.monotonic() - started_wait > args.wait_seconds:
                            raise RuntimeError(f"Timed out waiting for {problem_id}; it remains {run_info.get('status')}.")
                    snapshot = None
                    for attempt in range(settings.max_retries + 1):
                        try:
                            snapshot = await dashboard_board.call("read_blackboard", problem_id=problem_id)
                            break
                        except Exception:
                            if attempt >= settings.max_retries:
                                raise
                            await asyncio.sleep(0.2 * (attempt + 1))
                    if snapshot is None:
                        raise RuntimeError("Blackboard read returned no snapshot.")
                    measurement = assess(
                        case_id,
                        snapshot,
                        run_info,
                        answer_key,
                        configured_max_dispatches=settings.max_dispatches,
                    )
                    measurement["repetition"] = repetition
                    measurement["model_mode"] = settings.model_mode
                    measurements.append(measurement)
                except Exception as exc:
                    measurements.append({
                        "case_id": case_id,
                        "problem_id": problem_id,
                        "repetition": repetition,
                        "model_mode": settings.model_mode,
                        "status": run_info.get("status", "evaluation_error"),
                        "evaluation_error": f"{type(exc).__name__}: {str(exc)[:400]}",
                        "manual_review_required": True,
                    })

    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_mode": settings.model_mode,
        "model_provider": settings.model_provider if settings.model_mode == "live" else None,
        "provider_model": (settings.codex_model or "codex-default" if settings.model_provider == "codex_cli" else settings.model_name) if settings.model_mode == "live" else None,
        "repeat": args.repeat,
        "cases": measurements,
        "interpretation": "These are measured fixture-run checks, not a statistical performance claim. Review underlying evidence manually.",
    }
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    filename = f"evaluation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output = RESULT_ROOT / filename
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "model_mode": settings.model_mode, "cases": measurements}, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", dest="cases", action="append", help="Case ID; repeat to select several. Defaults to all cases.")
    parser.add_argument("--repeat", type=int, default=1, help="Fresh runs per selected case (default: 1).")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--wait-seconds", type=float, default=360)
    parser.add_argument("--allow-live", action="store_true", help="Allow provider calls if live mode is configured.")
    args = parser.parse_args()
    if args.repeat < 1 or args.poll_seconds <= 0 or args.wait_seconds <= 0:
        parser.error("repeat, poll-seconds, and wait-seconds must be positive")
    try:
        result = asyncio.run(evaluate(args))
    except Exception as exc:
        parser.exit(2, f"Evaluation stopped: {exc}\n")
    return 1 if any("evaluation_error" in item for item in result.get("cases", [])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
