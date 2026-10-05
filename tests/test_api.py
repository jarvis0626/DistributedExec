import pytest
from fastapi.testclient import TestClient
from distributedexec.api import create_app


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, 'admin-test', ['testserver', 'localhost', '127.0.0.1'])
    with TestClient(app) as client:
        yield client


def test_auth_host_origin_and_legacy_routes(api):
    assert api.get('/api/status').status_code == 401
    assert api.get('/health', headers={'host': 'evil.test'}).status_code == 400
    assert api.post('/api/run', headers={'origin': 'http://evil.test'}).status_code == 403
    assert api.post('/api/worker/register', json={'worker_id': 'fake'}).status_code == 404
    assert api.get('/api/download/worker.py').status_code == 404


def test_browser_bootstrap_csrf_and_ticket_single_use(api):
    ticket = api.post('/api/desktop/ticket', headers={'authorization': 'Bearer admin-test'}).json()['ticket']
    auth = api.post('/api/session', json={'ticket': ticket})
    assert auth.status_code == 200
    assert api.post('/api/session', json={'ticket': ticket}).status_code == 401
    assert api.get('/api/status').status_code == 200
    payload = {'code': 'def task(data): return data', 'dataset': []}
    assert api.post('/api/run', json=payload).status_code == 403
    response = api.post('/api/run', json=payload, headers={'x-csrf-token': auth.json()['csrf'], 'origin': 'http://testserver'})
    assert response.status_code == 200
    job = response.json()['job_id']
    assert api.get(f'/api/jobs/{job}/result').json() == []
    assert api.get('/api/jobs/../../secret/result').status_code == 404


def test_pair_rate_limit(api):
    for _ in range(6):
        response = api.post('/api/pair', json={'code': 'wrong', 'name': 'node'})
    assert response.status_code == 409
    assert 'rate limit' in response.json()['detail']


def test_artifact_symlink(api, tmp_path):
    headers = {'authorization': 'Bearer admin-test'}
    job = api.post('/api/run', json={'code': 'x', 'dataset': []}, headers=headers).json()['job_id']
    outside = tmp_path / 'outside.json'
    outside.write_text('secret')
    root = tmp_path / 'artifacts'
    root.mkdir()
    try:
        (root / (job + '.json')).symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    assert api.get(f'/api/jobs/{job}/result', headers=headers).status_code == 400
