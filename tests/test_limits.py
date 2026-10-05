import pytest
from test_store import store, register, submit, complete
from distributedexec.store import Conflict, Limits
from distributedexec.models import LogRequest


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


def test_unicode_logs_are_limited_by_utf8_bytes(store):
    store.limits = Limits(attempt_log_bytes=1024)
    worker = register(store)
    submit(store, [1])
    task = store.claim(worker)
    for seq in range(3):
        store.append_log(worker, task['attempt_id'], task['attempt_token'],
            LogRequest(seq=seq, text='\u4f60' * 100))
    with pytest.raises(Conflict, match='log limit'):
        store.append_log(worker, task['attempt_id'], task['attempt_token'],
            LogRequest(seq=3, text='\u4f60' * 100))


def test_workspace_size_counts_utf8_log_bytes(store):
    worker = register(store)
    submit(store, [1])
    task = store.claim(worker)
    with store.read() as conn:
        before = store.storage_size(conn)
    message = '\U0001f642' * 100
    store.append_log(worker, task['attempt_id'], task['attempt_token'], LogRequest(seq=0, text=message))
    with store.read() as conn:
        assert store.storage_size(conn) - before == len(message.encode('utf-8'))
