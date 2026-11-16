from __future__ import annotations

import pytest

from campus_blackboard.tools import FixtureTools, RunbookTools, ToolRegistry, read_case_summary


def test_fixture_tools_return_provenance_and_targeted_data() -> None:
    tools = FixtureTools()
    baseline = tools.query_metrics(
        "db_pool",
        ["request_latency_p95_ms", "request_error_rate_pct", "request_rate_per_min", "app_cpu_pct"],
    )
    targeted = tools.query_metrics(
        "db_pool", ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]
    )

    assert baseline["records"]
    assert {row["name"] for row in baseline["records"]}.isdisjoint(
        {"db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"}
    )
    assert [row["value"] for row in targeted["records"]] == [40, 40, 27]
    assert len(targeted["source_ids"]) == len(targeted["records"])
    assert all(row["source_id"] in targeted["source_ids"] for row in targeted["records"])
    assert targeted["tool_name"] == "query_metrics"


def test_tools_are_case_scoped_and_filter_logs() -> None:
    tools = ToolRegistry()
    result = tools.execute("query_logs", case_id="dns_fault", arguments={"contains": "NXDOMAIN"})
    assert result["records"]
    assert all("NXDOMAIN" in row["message"] for row in result["records"])
    assert all(row["source_id"].startswith("dns-") for row in result["records"])

    no_match = tools.execute("query_logs", case_id="missing_evidence", arguments={"contains": "pool saturation"})
    assert no_match["records"] == []
    assert no_match["source_ids"] == []


def test_fixture_identifiers_cannot_escape_fixture_root() -> None:
    with pytest.raises(ValueError):
        FixtureTools().query_metrics("../evaluation/answer_keys", ["ground_truth"])


def test_case_summary_excludes_tool_records() -> None:
    summary = read_case_summary("db_pool")
    assert summary["case_id"] == "db_pool"
    assert "timeouts" in summary["symptoms"]
    assert "metrics" not in summary
    assert "logs" not in summary


def test_runbook_retrieval_uses_stable_ids_and_real_local_text() -> None:
    tool = RunbookTools()
    db = tool.search("database connection pool active connections", limit=4)
    dns = tool.search("DNS NXDOMAIN authoritative resolver", limit=4)

    assert db["results"]
    assert db["results"][0]["runbook_id"] == "RB-DB-POOL-01"
    assert db["results"][0]["section_id"].startswith("RB-DB-POOL-01#")
    assert "runbooks/database-connection-pool.md" == db["results"][0]["source"]
    assert "pool" in db["results"][0]["text"].lower()
    assert dns["results"][0]["runbook_id"] == "RB-DNS-01"


def test_registry_rejects_non_allowlisted_tools() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        ToolRegistry().execute("read_file", arguments={"path": "evaluation/answer_keys/db_pool.json"})


@pytest.mark.parametrize("tool,query", [
    ("query_logs", {"contains": 7}), ("get_change_history", {"service": 7}),
    ("query_logs", {"limit": 0}), ("query_logs", {"limit": True}),
    ("query_logs", {"level": "fatal"}), ("query_metrics", {"contains": 7}),
    ("query_metrics", {"metric_names": ["x" * 81]}),
])
def test_fixture_execution_rejects_invalid_query_arguments(tool, query) -> None:
    with pytest.raises(ValueError):
        ToolRegistry().execute(tool, case_id="db_pool", arguments=query)


def test_query_catalog_exposes_names_without_evidence_values():
    from campus_blackboard.tools.fixtures import read_query_catalog
    catalog = read_query_catalog('db_pool')
    assert 'db_pool_active_connections' in catalog['metric_names']
    assert 'learning-portal' in catalog['services']
    assert 'records' not in catalog
    assert 'value' not in catalog
