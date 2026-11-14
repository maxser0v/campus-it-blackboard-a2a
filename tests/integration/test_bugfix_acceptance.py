"""Independent evaluator gate over real isolated A2A missing-evidence flow."""
from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from campus_blackboard.blackboard.client import BlackboardClient
from scripts.evaluate import assess
from tests.integration.test_acceptance_independent import local_services  # noqa: F401


def test_real_missing_evidence_satisfies_strict_evaluator(local_services):
    controller = local_services['controller']
    response = httpx.post(f'{controller}/runs', json={
        'case_id': 'missing_evidence', 'client_request_id': str(uuid.uuid4()),
    }, timeout=10)
    response.raise_for_status()
    problem_id = response.json()['problem_id']
    deadline = time.monotonic() + 60
    while True:
        response = httpx.get(f'{controller}/runs/{problem_id}', timeout=10)
        response.raise_for_status()
        run = response.json()
        if run.get('result') is not None:
            break
        assert time.monotonic() < deadline
        time.sleep(0.1)
    board = BlackboardClient(local_services['blackboard'], actor='dashboard',
        api_key=local_services['credentials']['dashboard'], timeout=15)
    snapshot = asyncio.run(board.call('read_blackboard', problem_id=problem_id))
    result = assess('missing_evidence', snapshot, run, {})
    checks = result['checks']
    assert result['operational_failure'] is False
    assert checks['terminal_status_matches_case_expectation']
    assert checks['missing_evidence_unavailable_request_recorded'], result
    assert checks['missing_evidence_stopped_for_evidence_reason'], result
    assert checks['missing_evidence_ends_without_accepted_cause'], result
