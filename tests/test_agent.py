import concurrent.futures
import json
import threading
from pathlib import Path

import pytest
from distributedexec import agent


def windows_error(code):
    error = PermissionError('A Windows reader is holding the destination')
    error.winerror = code
    return error


@pytest.mark.parametrize('code', [5, 32, 33])
def test_atomic_write_retries_windows_sharing_errors(tmp_path, monkeypatch, code):
    target = tmp_path / 'state.json'
    target.write_text('{"old":true}', encoding='utf-8')
    original = Path.replace
    calls, delays = [], []

    def replace(staging, destination):
        calls.append(staging)
        if len(calls) <= 2:
            assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}
            raise windows_error(code)
        return original(staging, destination)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(agent.time, 'sleep', delays.append)
    agent.write_json(target, {'new': True})
    assert json.loads(target.read_text(encoding='utf-8')) == {'new': True}
    assert len(calls) == 3
    assert len(delays) == 2
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('code', [5, 13])
def test_atomic_write_failure_preserves_destination_and_cleans_staging(tmp_path, monkeypatch, code):
    target = tmp_path / 'state.json'
    target.write_text('{"old":true}', encoding='utf-8')
    calls, delays = [], []

    def replace(staging, destination):
        calls.append(staging)
        raise windows_error(code)

    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(agent.time, 'sleep', delays.append)
    with pytest.raises(PermissionError):
        agent.write_json(target, {'new': True})
    assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}
    assert len(calls) == (6 if code == 5 else 1)
    assert len(delays) == (5 if code == 5 else 0)
    assert not list(tmp_path.glob('*.tmp'))


def test_concurrent_atomic_writers_use_separate_staging_files(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    barrier = threading.Barrier(2)
    original = agent.private_file
    staged = []

    def stage(path, content):
        staged.append(path)
        original(path, content)
        barrier.wait(timeout=5)

    monkeypatch.setattr(agent, 'private_file', stage)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(agent.write_json, target, {'writer': value}) for value in (1, 2)]
        for future in futures:
            future.result()
    assert len(set(staged)) == 2
    assert json.loads(target.read_text(encoding='utf-8')) in [{'writer': 1}, {'writer': 2}]
    assert not list(tmp_path.glob('*.tmp'))
