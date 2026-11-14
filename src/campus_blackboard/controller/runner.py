"""Bounded controller loop that re-reads the Blackboard after every task."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from .scheduler import DispatchPlan, TerminalPlan, choose_next_action


Blackboard = Any
DispatchAgent = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RunResult:
    problem_id: str
    status: str
    reason: str
    dispatch_count: int
    started_at: str
    finished_at: str
    last_dispatch_id: str | None = None
    error: str | None = None
    observed_model_tokens: int = 0
    model_usage_state: str = "unmeasured"
    max_run_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_id() -> str:
    return str(uuid.uuid4())


def _dispatch_events(entries: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    for entry in entries:
        if entry.get("knowledge_type") != "controller_decision":
            continue
        data = entry.get("data", {})
        if isinstance(data, Mapping) and str(data.get("event_type", "")).startswith("dispatch_"):
            events.append(data)
    return events


def _problem_limits(
    snapshot: Mapping[str, Any], configured: Mapping[str, Any]
) -> tuple[int, float, int | None]:
    limits = snapshot.get("problem", {}).get("limits", {})
    if not isinstance(limits, Mapping):
        limits = {}
    max_dispatches = int(limits.get("max_dispatches", configured.get("max_dispatches", 12)))
    timeout_seconds = float(limits.get("timeout_seconds", configured.get("timeout_seconds", 300)))
    token_value = limits.get("max_run_tokens", configured.get("max_run_tokens"))
    max_run_tokens = int(token_value) if token_value is not None else None
    return max(1, max_dispatches), max(1.0, timeout_seconds), max(1, max_run_tokens) if max_run_tokens is not None else None


def _usage_total(result: Mapping[str, Any]) -> tuple[int, bool, list[dict[str, Any]]]:
    """Extract provider-reported tokens only; missing usage remains unknown."""
    records = result.get("model_usage", [])
    if not isinstance(records, list):
        return 0, False, []
    total = 0
    measured_any = False
    complete = bool(records)
    clean: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            complete = False
            continue
        usage = record.get("usage")
        count: int | None = None
        if isinstance(usage, Mapping):
            direct = usage.get("total_tokens")
            prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
            completion = usage.get("completion_tokens", usage.get("output_tokens"))
            if isinstance(direct, int) and not isinstance(direct, bool) and direct >= 0:
                count = direct
            elif (
                isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0
                and isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0
            ):
                count = prompt + completion
        if count is None:
            complete = False
        else:
            total += count
            measured_any = True
        clean.append({
            key: record.get(key)
            for key in ("mode", "model", "duration_ms", "usage")
            if key in record
        })
    return total, bool(measured_any and complete), clean


class ControllerRunner:
    """Run one investigation with one active specialist task at a time.

    `dispatch_agent(role, payload)` is injected by the service layer and uses
    the project's A2A client. Tests can supply a deterministic offline handler.
    """

    def __init__(
        self,
        blackboard: Blackboard,
        dispatch_agent: DispatchAgent,
        *,
        max_dispatches: int = 12,
        timeout_seconds: float = 300,
        max_run_tokens: int | None = 12000,
    ) -> None:
        self.blackboard = blackboard
        self.dispatch_agent = dispatch_agent
        self.limits = {
            "max_dispatches": max(1, int(max_dispatches)),
            "timeout_seconds": max(1.0, float(timeout_seconds)),
            "max_run_tokens": max(1, int(max_run_tokens)) if max_run_tokens is not None else None,
        }

    async def _call(self, action: str, **params: Any) -> dict[str, Any]:
        value = self.blackboard.call(action, **params)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, dict):
            raise TypeError(f"Blackboard action {action!r} returned a non-object result")
        return value

    async def _snapshot(self, problem_id: str) -> dict[str, Any]:
        return await self._call("read_blackboard", problem_id=problem_id)

    async def _record_decision(self, problem_id: str, data: dict[str, Any]) -> None:
        await self._call(
            "write_blackboard",
            problem_id=problem_id,
            knowledge_type="controller_decision",
            data=data,
            request_id=_request_id(),
        )

    async def _set_status(self, problem_id: str, status: str) -> None:
        await self._call(
            "set_problem_status",
            problem_id=problem_id,
            status=status,
            request_id=_request_id(),
        )

    async def run(self, problem_id: str) -> RunResult:
        started_at = _utc_now()
        monotonic_start = time.monotonic()
        deadline = monotonic_start + self.limits["timeout_seconds"]
        dispatch_count = 0
        last_dispatch_id = None
        observed_model_tokens = 0
        token_usage_complete = True
        token_usage_seen = False
        max_run_tokens = self.limits["max_run_tokens"]
        active_plan = None
        error = None

        async def bounded(operation: Callable[[], Awaitable[Any]]) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(operation(), timeout=remaining)

        try:
            snapshot = await bounded(lambda: self._snapshot(problem_id))
            max_dispatches, timeout_seconds, max_run_tokens = _problem_limits(snapshot, self.limits)
            deadline = monotonic_start + timeout_seconds
            await bounded(lambda: self._set_status(problem_id, "investigating"))
            while True:
                snapshot = await bounded(lambda: self._snapshot(problem_id))
                existing_events = _dispatch_events(snapshot.get("entries", []))
                dispatch_count = max(dispatch_count, len({
                    str(event["dispatch_id"]) for event in existing_events
                    if event.get("event_type") == "dispatch_started" and event.get("dispatch_id")
                }))
                plan = choose_next_action(snapshot, dispatch_count=dispatch_count, max_dispatches=max_dispatches)
                # A final successful review can consume the exact remaining budget.
                if not (isinstance(plan, TerminalPlan) and plan.status == "completed"):
                    if max_run_tokens is not None and observed_model_tokens >= max_run_tokens:
                        plan = TerminalPlan("unresolved", "model_token_limit_reached")
                if isinstance(plan, TerminalPlan):
                    terminal = plan
                    break

                dispatch_id = str(uuid.uuid4())
                last_dispatch_id = dispatch_id
                active_plan = plan
                await bounded(lambda: self._record_decision(problem_id, {
                    "event_type": "dispatch_started", "dispatch_id": dispatch_id,
                    "dispatch_key": plan.dispatch_key, "agent": plan.role,
                    "purpose": plan.purpose, "trigger": plan.trigger,
                    "board_revision": snapshot.get("problem", {}).get("board_revision"),
                    "started_at": _utc_now(),
                }))
                dispatch_count += 1
                result = await bounded(lambda: self.dispatch_agent(plan.role, {
                    "problem_id": problem_id, "purpose": plan.purpose, "dispatch_id": dispatch_id,
                }))
                if not isinstance(result, dict) or result.get("ok", True) is False:
                    raise RuntimeError(str(result.get("error", "specialist returned an unsuccessful result"))
                                       if isinstance(result, dict) else "specialist returned a non-object result")
                task_tokens, task_usage_complete, model_usage = _usage_total(result)
                observed_model_tokens += task_tokens
                token_usage_seen = token_usage_seen or task_tokens > 0
                token_usage_complete = token_usage_complete and task_usage_complete
                metadata = result.get("_a2a", {})
                if not isinstance(metadata, Mapping):
                    metadata = {}
                await bounded(lambda: self._record_decision(problem_id, {
                    "event_type": "dispatch_finished", "dispatch_id": dispatch_id,
                    "dispatch_key": plan.dispatch_key, "agent": plan.role,
                    "purpose": plan.purpose, "status": "completed",
                    "result_entry_ids": result.get("entry_ids", []),
                    "task_id": result.get("task_id") or metadata.get("task_id"),
                    "context_id": result.get("context_id") or metadata.get("context_id"),
                    "task_status": metadata.get("status") or metadata.get("state"),
                    "summary": str(result.get("summary", ""))[:500],
                    "model_usage": model_usage, "observed_model_tokens": task_tokens,
                    "model_usage_complete": task_usage_complete, "finished_at": _utc_now(),
                }))
                active_plan = None
        except asyncio.TimeoutError:
            terminal = TerminalPlan("unresolved", "case_deadline_reached")
            error = "case_deadline_reached"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            terminal = TerminalPlan("blocked", "specialist_or_transport_failure" if active_plan else "controller_or_blackboard_failure")

        # Terminal persistence gets its own short allowance, never more specialist
        # work. A stalled board must not leave the in-memory task running forever.
        async def bookkeeping() -> None:
            if error and active_plan and last_dispatch_id:
                await self._record_failure(problem_id, last_dispatch_id, active_plan, error)
            await self._finish(problem_id, terminal)

        try:
            await asyncio.wait_for(bookkeeping(), timeout=2.0)
        except Exception as exc:
            detail = f"terminal bookkeeping failed: {type(exc).__name__}"
            error = f"{error}; {detail}" if error else detail
        return self._result(
            problem_id, terminal, dispatch_count, started_at, last_dispatch_id,
            error=error, observed_model_tokens=observed_model_tokens,
            token_usage_seen=token_usage_seen, token_usage_complete=token_usage_complete,
            max_run_tokens=max_run_tokens,
        )

    async def _record_failure(
        self, problem_id: str, dispatch_id: str, plan: DispatchPlan, error: str
    ) -> None:
        await self._record_decision(
            problem_id,
            {
                "event_type": "dispatch_finished",
                "dispatch_id": dispatch_id,
                "dispatch_key": plan.dispatch_key,
                "agent": plan.role,
                "purpose": plan.purpose,
                "status": "failed",
                "error": error,
                "finished_at": _utc_now(),
            },
        )

    async def _finish(self, problem_id: str, plan: TerminalPlan) -> None:
        await self._record_decision(
            problem_id,
            {
                "event_type": "terminal_decision",
                "status": plan.status,
                "reason": plan.reason,
                "decided_at": _utc_now(),
            },
        )
        await self._set_status(problem_id, plan.status)

    @staticmethod
    def _result(
        problem_id: str,
        plan: TerminalPlan,
        dispatch_count: int,
        started_at: str,
        last_dispatch_id: str | None,
        *,
        error: str | None = None,
        observed_model_tokens: int = 0,
        token_usage_seen: bool = False,
        token_usage_complete: bool = False,
        max_run_tokens: int | None = None,
    ) -> RunResult:
        return RunResult(
            problem_id=problem_id,
            status=plan.status,
            reason=plan.reason,
            dispatch_count=dispatch_count,
            started_at=started_at,
            finished_at=_utc_now(),
            last_dispatch_id=last_dispatch_id,
            error=error,
            observed_model_tokens=observed_model_tokens,
            model_usage_state=(
                "complete" if token_usage_seen and token_usage_complete
                else "partial" if token_usage_seen
                else "unmeasured"
            ),
            max_run_tokens=max_run_tokens,
        )
