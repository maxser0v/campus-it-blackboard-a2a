"""Compare query meaning without running tools or reading hidden evidence."""
import json
import re


def signature(tool, query):
    if tool not in {'query_metrics', 'query_logs', 'get_change_history'} or not isinstance(query, dict):
        return None
    q = dict(query)
    if tool == 'query_metrics':
        q = {'metric_names': sorted(set(q.get('metric_names') or [])), 'contains': q.get('contains')}
    else:
        q = {'service': None, 'limit': 20, **q}
        if tool == 'query_logs':
            q = {'contains': None, 'level': None, **q}
    if 'contains' in q:
        q['contains'] = sorted(set(re.findall(r'[a-z0-9_]+', (q['contains'] or '').lower())))
    if 'limit' in q and isinstance(q['limit'], (int, float)):
        q['limit'] = int(q['limit'])
    return tool, json.dumps(q, sort_keys=True)


def evidence_queries(entries):
    for entry in entries:
        if entry.get('knowledge_type') != 'observation':
            continue
        data = entry.get('data', {})
        yield data
        yield from data.get('components', [])


def already_observed(tool, query, entries):
    key = signature(tool, query)
    if key is None:
        return False
    return any(signature(item.get('tool_name'), item.get('query')) == key for item in evidence_queries(entries))
