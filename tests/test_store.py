import concurrent.futures
import pytest
from pydantic import ValidationError
from distributedexec.models import RunRequest, PairRequest, Heartbeat, Completion, LogRequest
from distributedexec.store import Store, Conflict, LEASE_SECONDS, run


@pytest.fixture
def store(tmp_path):
    clock = [1000.0]
    database = Store(tmp_path, clock=lambda: clock[0])
    database.time = clock
    yield database
    database.engine.dispose()


def register(store, name='worker', **budgets):
    code = store.rotate_pairing()['code']
    credential = store.pair(PairRequest(code=code, name=name, **budgets), 'local')['credential']
    store.heartbeat(credential, Heartbeat(runtime_id='sha256:test'))
    return credential


def submit(store, data=None, **settings):
    return store.submit(RunRequest(code='def task(data): return data', dataset=data if data is not None else [1, 2, 3, 4], chunk_size=1, **settings))


def complete(store, worker, task, result=None, **settings):
    request = Completion(status='succeeded', result=result if result is not None else task['data'], runtime_id='sha256:test', **settings)
    return store.complete(worker, task['attempt_id'], task['attempt_token'], request)


def test_empty_and_invalid(store):
    job = submit(store, [])
    assert store.job(job)['status'] == 'succeeded'
    assert store.result(job) == []
    with pytest.raises(ValidationError):
        RunRequest(code='x', dataset={})
    with pytest.raises(ValidationError):
        RunRequest(code='x', dataset=[], function_name='x.y')
    with pytest.raises(ValueError):
        submit(store, [float('nan')])


def test_ordering_duplicate_and_submission_key(store):
    worker = register(store, concurrency=4, cpu=4)
    req = RunRequest(code='def task(data): return data', dataset=[1, 2, 3], chunk_size=1)
    job = store.submit(req, 'same')
    assert store.submit(req, 'same') == job
    with pytest.raises(Conflict):
        store.submit(req.model_copy(update={'dataset': [7]}), 'same')
    tasks = [store.claim(worker) for _ in range(3)]
    for task in reversed(tasks):
        assert not complete(store, worker, task)['duplicate']
        assert complete(store, worker, task)['duplicate']
    assert store.result(job) == [1, 2, 3]
    assert store.job(job)['completed_chunks'] == 3


def test_concurrent_claims(store):
    workers = [register(store, str(i), cpu=8, concurrency=8, memory_mb=8192) for i in range(4)]
    job = submit(store, list(range(24)))
    def claim(index):
        return store.claim(workers[index % 4])
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        tasks = list(pool.map(claim, range(24)))
    assert all(tasks)
    assert len({t['chunk_id'] for t in tasks}) == 24
    assert len(store.job(job)['attempts']) == 24


def test_budget_priority_and_fifo(store):
    worker = register(store, cpu=1, memory_mb=256, concurrency=8)
    submit(store, [99], cpu=2, priority=10)
    low = submit(store, [1], priority=-1)
    high = submit(store, [2], priority=3)
    task = store.claim(worker)
    assert task['job_id'] == high
    assert store.claim(worker) is None
    complete(store, worker, task)
    assert store.claim(worker)['job_id'] == low


def test_busy_heartbeat_and_lease_recovery(store):
    worker = register(store)
    second = register(store, 'second')
    job = submit(store, [7])
    task = store.claim(worker)
    for _ in range(8):
        store.time[0] += 10
        renew = store.heartbeat(worker, Heartbeat(attempts=[task['attempt_id']], runtime_id='sha256:test'))
        assert task['attempt_id'] in renew['renewed']
        store.reap()
        assert store.status()['workers'][0]['online']
        assert store.job(job)['chunks'][0]['state'] == 'running'
    store.time[0] += LEASE_SECONDS + 1
    store.reap()
    with pytest.raises(Conflict):
        complete(store, worker, task)
    store.time[0] += 4
    store.heartbeat(second, Heartbeat(runtime_id='sha256:test'))
    replacement = store.claim(second)
    assert replacement['chunk_id'] == task['chunk_id']
    assert replacement['attempt_token'] != task['attempt_token']
    complete(store, second, replacement)
    assert store.result(job) == [7]


def test_fifo_when_submission_timestamps_tie(store):
    worker = register(store, cpu=1, concurrency=1)
    earlier = submit(store, [1, 2])
    later = submit(store, [3])
    for expected in [earlier, earlier, later]:
        task = store.claim(worker)
        assert task['job_id'] == expected
        complete(store, worker, task)


def test_ownership_revoke_and_expired_credential(store):
    worker = register(store)
    second = register(store, 'second')
    submit(store, [1])
    task = store.claim(worker)
    with pytest.raises(PermissionError):
        complete(store, second, task)
    with store.tx() as conn:
        run(conn, 'UPDATE workers SET revoked=1 WHERE credential_hash=:h', h=__import__('distributedexec.store', fromlist=['digest']).digest(worker))
    with pytest.raises(PermissionError):
        complete(store, worker, task)
    store.time[0] += 31 * 86400
    with pytest.raises(PermissionError):
        store.claim(second)


def test_cancel_and_failed_jobs_never_resurrect(store):
    worker = register(store, concurrency=2, cpu=2)
    job = submit(store, [1, 2])
    tasks = [store.claim(worker), store.claim(worker)]
    store.cancel(job)
    with pytest.raises(Conflict):
        complete(store, worker, tasks[0])
    store.time[0] += 60
    store.reap()
    assert store.job(job)['status'] == 'cancelled'
    store.heartbeat(worker, Heartbeat(runtime_id='sha256:test'))
    job = submit(store, [1, 2])
    tasks = [store.claim(worker), store.claim(worker)]
    store.complete(worker, tasks[0]['attempt_id'], tasks[0]['attempt_token'], Completion(status='script_error', error='deliberate', runtime_id='test'))
    with pytest.raises(Conflict):
        complete(store, worker, tasks[1])
    store.reap()
    assert store.job(job)['status'] == 'failed'


def test_log_retransmission(store):
    worker = register(store)
    submit(store, [1])
    task = store.claim(worker)
    log = LogRequest(seq=0, text='hello')
    store.append_log(worker, task['attempt_id'], task['attempt_token'], log)
    store.append_log(worker, task['attempt_id'], task['attempt_token'], log)
    with pytest.raises(Conflict):
        store.append_log(worker, task['attempt_id'], task['attempt_token'], LogRequest(seq=0, text='changed'))
    with store.read() as conn:
        assert conn.exec_driver_sql('SELECT COUNT(*) FROM logs').scalar() == 1


def test_restart_and_bounded_retries(store):
    worker = register(store)
    job = submit(store, [3])
    task = store.claim(worker)
    store.engine.dispose()
    reopened = Store(store.workspace, clock=store.clock)
    try:
        assert reopened.job(job)['status'] == 'running'
        for _ in range(3):
            store.time[0] += 31
            reopened.reap()
            store.time[0] += 10
            reopened.heartbeat(worker, Heartbeat(runtime_id='test'))
            task = reopened.claim(worker)
        assert task is None
        assert reopened.job(job)['status'] == 'failed'
        assert len(reopened.job(job)['attempts']) == 3
    finally:
        reopened.engine.dispose()
