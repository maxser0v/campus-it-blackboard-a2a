"""Read-only log, metric, and change-history tools for synthetic incidents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_CASE_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures"


class FixtureError(ValueError):
    """Raised for invalid fixture identifiers or tool arguments."""


def validate_fixture_arguments(tool: str, query: dict[str, Any]) -> None:
    """Validate the same query contract before model repair and execution."""
    allowed = {
        "query_metrics": {"metric_names", "contains"},
        "query_logs": {"contains", "service", "level", "limit"},
        "get_change_history": {"service", "limit"},
    }
    if tool not in allowed or not isinstance(query, dict) or set(query) - allowed[tool]:
        raise FixtureError("unsupported fixture tool or query arguments")
    for key, maximum in (("contains", 100 if tool == "query_metrics" else 200), ("service", 100)):
        value = query.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise FixtureError(f"{key} must be a short string")
    if "limit" in query:
        value = query["limit"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise FixtureError("limit must be an integer between 1 and 100")
    level = query.get("level")
    if level is not None and (not isinstance(level, str) or level not in {"debug", "info", "warning", "error", "critical"}):
        raise FixtureError("unsupported log level")
    if tool == "query_metrics":
        names = query.get("metric_names")
        if names is not None and (not isinstance(names, (list, tuple)) or len(names) > 20 or any(not isinstance(name, str) or not name or len(name) > 80 for name in names)):
            raise FixtureError("metric_names must contain at most 20 short non-empty strings")


def load_case(case_id: str) -> dict[str, Any]:
    """Load one known case file; caller input is never used as a filesystem path."""
    if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
        raise FixtureError("case_id must be a lower-case fixture identifier")
    path = _FIXTURE_ROOT / case_id / "case.json"
    if not path.is_file():
        raise FixtureError(f"unknown synthetic case: {case_id}")
    with path.open("r", encoding="utf-8") as handle:
        content = json.load(handle)
    if content.get("case_id") != case_id:
        raise FixtureError("fixture identity does not match its directory")
    return content


def read_case_summary(case_id: str) -> dict[str, str]:
    """Return only incident metadata, never underlying metric/log/change rows."""
    case = load_case(case_id)
    return {
        "case_id": case["case_id"],
        "title": str(case.get("title", "")),
        "symptoms": str(case.get("symptoms", "")),
        "observed_at": str(case.get("observed_at", "")),
    }


def _limit(value: int, default: int = 25) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return min(value, 100)


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part for part in re.findall(r"[a-z0-9_]+", value.lower()) if len(part) > 1}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class FixtureTools:
    """Constrained query surface over local JSON fixtures, with provenance."""

    def query_metrics(
        self,
        case_id: str,
        metric_names: Iterable[str] | None = None,
        *,
        contains: str | None = None,
    ) -> dict[str, Any]:
        validate_fixture_arguments("query_metrics", {"metric_names": metric_names, "contains": contains})
        case = load_case(case_id)
        names = set(metric_names or ())
        if any(not isinstance(name, str) or len(name) > 80 for name in names):
            raise FixtureError("metric_names must contain short strings")
        if contains is not None and (not isinstance(contains, str) or len(contains) > 100):
            raise FixtureError("contains must be a short string")
        contains_terms = _tokens(contains)
        records = []
        for row in case.get("metrics", []):
            if names and row.get("name") not in names:
                continue
            if contains_terms and not contains_terms.intersection(_tokens(row.get("name"))):
                continue
            records.append(dict(row))
        records = records[:50]
        query = {"metric_names": sorted(names), "contains": contains}
        return {
            "tool_name": "query_metrics",
            "case_id": case_id,
            "query": query,
            "source_ids": [row["source_id"] for row in records],
            "observed_at": _now(),
            "records": records,
        }

    def query_logs(
        self,
        case_id: str,
        *,
        contains: str | None = None,
        service: str | None = None,
        level: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        validate_fixture_arguments("query_logs", {"contains": contains, "service": service, "level": level, "limit": limit})
        case = load_case(case_id)
        if contains is not None and (not isinstance(contains, str) or len(contains) > 200):
            raise FixtureError("contains must be a short string")
        if service is not None and (not isinstance(service, str) or len(service) > 100):
            raise FixtureError("service must be a short string")
        if level is not None and level not in {"debug", "info", "warning", "error", "critical"}:
            raise FixtureError("unsupported log level")
        terms = _tokens(contains)
        records = []
        for row in case.get("logs", []):
            if service and row.get("service") != service:
                continue
            if level and row.get("level") != level:
                continue
            searchable = " ".join(
                [str(row.get("message", "")), str(row.get("service", "")), json.dumps(row.get("fields", {}))]
            )
            if terms and not terms.issubset(_tokens(searchable)):
                continue
            records.append(dict(row))
        records = records[:_limit(limit)]
        query = {"contains": contains, "service": service, "level": level, "limit": _limit(limit)}
        return {
            "tool_name": "query_logs",
            "case_id": case_id,
            "query": query,
            "source_ids": [row["source_id"] for row in records],
            "observed_at": _now(),
            "records": records,
        }

    def get_change_history(
        self, case_id: str, *, service: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        validate_fixture_arguments("get_change_history", {"service": service, "limit": limit})
        case = load_case(case_id)
        if service is not None and (not isinstance(service, str) or len(service) > 100):
            raise FixtureError("service must be a short string")
        records = [
            dict(row)
            for row in case.get("changes", [])
            if not service or row.get("service") == service
        ][:_limit(limit)]
        query = {"service": service, "limit": _limit(limit)}
        return {
            "tool_name": "get_change_history",
            "case_id": case_id,
            "query": query,
            "source_ids": [row["source_id"] for row in records],
            "observed_at": _now(),
            "records": records,
        }
