import concurrent.futures
import pytest
from test_store import store, register, submit, complete
from distributedexec.store import Conflict
from distributedexec.models import Heartbeat


def test_completion_cancel_race(store):
    worker = register(store)
    for _ in range(10):
        job = submit(store, [1])
        task = store.claim(worker)
        def finish():
            try:
                complete(store, worker, task)
            except Conflict:
                pass
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(finish), pool.submit(store.cancel, job)]
            for future in futures:
                future.result()
        result = store.job(job)
        assert result['status'] in ('succeeded', 'cancelled')
        if result['cancel_requested']:
            assert result['status'] == 'cancelled'


def test_cancel_reaper_race(store):
    worker = register(store)
    job = submit(store, [1])
    store.claim(worker)
    store.time[0] += 31
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(store.reap), pool.submit(store.cancel, job)]
        for future in futures:
            future.result()
    store.time[0] += 10
    store.heartbeat(worker, Heartbeat(runtime_id='test'))
    assert store.claim(worker) is None
    assert store.job(job)['status'] == 'cancelled'
