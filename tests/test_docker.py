import threading
import uuid
import pytest
from distributedexec.runtime import DockerExecutor, selected_endpoint, check_runtime, client_for
from distributedexec.store import digest, encode

pytestmark = pytest.mark.docker


@pytest.fixture
def executor(tmp_path):
    endpoint = selected_endpoint()[1]
    if check_runtime(endpoint)['state'] != 'ready':
        pytest.skip('Prepare a local Linux Docker runtime before integration tests')
    worker = str(uuid.uuid4())
    value = DockerExecutor(endpoint, worker, tmp_path / 'attempts')
    yield value
    value.reconcile()


def task(code, data=None, **settings):
    data = [1, 2] if data is None else data
    return {'attempt_id': str(uuid.uuid4()), 'code': code, 'data': data, 'function_name': 'task',
        'cpu': 1, 'memory_mb': 128, 'timeout': 20, 'workspace_id': 'test',
        'code_hash': digest(code), 'input_hash': digest(encode(data)), **settings}


def test_success_logs_and_cleanup(executor):
    logs, persisted = [], []
    result = executor.execute(task('def task(data):\n    import sys\n    print("hello")\n    print("error stream", file=sys.stderr)\n    return [x * 2 for x in data]'),
        threading.Event(), lambda stream, line: logs.append((stream, line)), persist=persisted.append)
    assert result['status'] == 'succeeded', result
    assert result['result'] == [2, 4]
    assert any(name == 'stdout' and 'hello' in text for name, text in logs)
    assert any(name == 'stderr' and 'error stream' in text for name, text in logs)
    assert persisted == [result]
    assert list(executor.root.iterdir()) == []


@pytest.mark.parametrize('code,status', [
    ('def task(data):\n    raise RuntimeError("intentional")', 'script_error'),
    ('def task(data):\n    return {"wrong": True}', 'invalid_result'),
    ('def task(data):\n    return [float("nan")]', 'invalid_result'),
    ('def task(data):\n    import time\n    time.sleep(60)\n    return data', 'timeout'),
    ('def task(data):\n    x = [bytearray(16 * 1024 * 1024) for _ in range(64)]\n    return data', 'oom'),
])
def test_failures(executor, code, status):
    response = executor.execute(task(code, timeout=2 if status == 'timeout' else 20, memory_mb=64 if status == 'oom' else 128), threading.Event())
    assert response['status'] == status, response
    assert response['exit_code'] is not None
    assert list(executor.root.iterdir()) == []


def test_cancellation_and_hardening(executor):
    stop = threading.Event()
    result = []
    job = task('def task(data):\n    import time\n    time.sleep(60)\n    return data')
    thread = threading.Thread(target=lambda: result.append(executor.execute(job, stop)))
    thread.start()
    client = client_for(executor.endpoint)
    try:
        import time
        containers = []
        for _ in range(60):
            containers = client.containers.list(filters={'label': f'org.distributedexec.attempt={job["attempt_id"]}'})
            if containers:
                break
            time.sleep(0.1)
        assert containers
        host = containers[0].attrs['HostConfig']
        assert host['ReadonlyRootfs']
        assert host['NetworkMode'] == 'none'
        assert host['Memory'] == host['MemorySwap'] == 128 * 1048576
        assert host['PidsLimit'] == 64
        assert host['CapDrop'] == ['ALL']
        assert containers[0].attrs['Config']['User'] == '65532:65532'
        stop.set()
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert result[0]['status'] == 'interrupted'
        assert not client.containers.list(all=True, filters={'label': f'org.distributedexec.attempt={job["attempt_id"]}'})
    finally:
        stop.set()
        thread.join(timeout=20)
        client.close()


def test_end_to_end_sqlite_container_order(executor, tmp_path):
    from distributedexec.store import Store
    from distributedexec.models import RunRequest, PairRequest, Heartbeat, Completion
    database = Store(tmp_path / 'workspace')
    try:
        pairing = database.rotate_pairing()
        worker = database.pair(PairRequest(code=pairing['code'], name='integration', cpu=2, concurrency=2), 'local')['credential']
        database.heartbeat(worker, Heartbeat(runtime_id=check_runtime(executor.endpoint)['image_id']))
        job = database.submit(RunRequest(code='def task(data): return [x * 3 for x in data]', dataset=[1, 2, 3, 4], chunk_size=2))
        tasks = [database.claim(worker), database.claim(worker)]
        for chunk in reversed(tasks):
            response = executor.execute(chunk, threading.Event())
            assert response['status'] == 'succeeded', response
            database.complete(worker, chunk['attempt_id'], chunk['attempt_token'], Completion.model_validate(response))
        assert database.result(job) == [3, 6, 9, 12]
    finally:
        database.engine.dispose()
