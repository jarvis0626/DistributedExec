import pytest
from test_store import store, register, submit, complete
from distributedexec.store import Conflict, Limits


def test_configured_output_limit_fails_without_retry(store):
    store.limits = Limits(chunk_output_bytes=1024)
    worker = register(store)
    job = submit(store, [1])
    task = store.claim(worker)
    complete(store, worker, task, ['x' * 2048])
    detail = store.job(job)
    assert detail['status'] == 'failed'
    assert detail['attempts'][0]['state'] == 'invalid_result'
    store.time[0] += 60
    store.reap()
    assert store.claim(worker) is None


def test_combined_output_limit(store):
    store.limits = Limits(job_output_bytes=1024)
    worker = register(store)
    job = submit(store, [1, 2])
    complete(store, worker, store.claim(worker), ['x' * 700])
    complete(store, worker, store.claim(worker), ['y' * 700])
    assert store.job(job)['status'] == 'failed'


def test_chunk_count_limit(store):
    store.limits = Limits(chunks_per_job=2)
    with pytest.raises(Conflict):
        submit(store, [1, 2, 3])
