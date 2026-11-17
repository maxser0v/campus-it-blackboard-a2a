from campus_blackboard.tools.queries import already_observed


def test_complete_baseline_reuses_filters_but_not_missing_metrics():
    entries = [{'knowledge_type': 'observation', 'data': {'components': [
        {'tool_name': 'query_metrics', 'query': {}, 'records': [{'name': 'latency'}]},
        {'tool_name': 'query_logs', 'query': {'limit': 20}, 'records': [{'message': 'error'}]},
    ]}}]
    assert already_observed('query_metrics', {'metric_names': ['latency']}, entries)
    assert already_observed('query_logs', {'contains': 'error'}, entries)
    assert not already_observed('query_metrics', {'metric_names': ['dependency_health']}, entries)


def test_truncated_baseline_does_not_hide_unseen_logs():
    entries = [{'knowledge_type': 'observation', 'data': {'tool_name': 'query_logs', 'query': {'limit': 20}, 'records': [{}] * 20}}]
    assert not already_observed('query_logs', {'contains': 'dependency'}, entries)
