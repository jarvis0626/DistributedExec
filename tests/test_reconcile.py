from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest
import requests

from distributedexec import runtime


def client(*, containers=(), failure=None):
    listing = Mock(return_value=list(containers), side_effect=failure)
    return SimpleNamespace(containers=SimpleNamespace(list=listing), close=Mock())


@pytest.mark.parametrize('failure', [requests.Timeout('Docker stalled'),
                                   requests.ConnectionError('Docker disconnected')])
def test_reconcile_retries_transport_failure_with_same_ownership_filters(tmp_path, monkeypatch, failure):
    executor = runtime.DockerExecutor('npipe://test', 'worker-one', root=tmp_path)
    owned = Mock()
    first, second = client(failure=failure), client(containers=[owned])
    factory = Mock(side_effect=[first, second])
    sleeps = []
    monkeypatch.setattr(runtime, 'client_for', factory)
    monkeypatch.setattr(runtime.time, 'sleep', sleeps.append)
    executor.reconcile()
    for connection in (first, second):
        connection.containers.list.assert_called_once_with(all=True, filters={'label': [
            'org.distributedexec.app=DistributedExec', 'org.distributedexec.worker=worker-one']})
        connection.close.assert_called_once_with()
    owned.remove.assert_called_once_with(force=True)
    assert factory.call_count == 2 and sleeps == [.2]


def test_reconcile_closes_failed_client_before_backoff_and_retains_files_until_success(tmp_path, monkeypatch):
    executor = runtime.DockerExecutor('unix:///test', 'worker-one', root=tmp_path)
    attempt = tmp_path / 'unfinished-attempt'
    attempt.mkdir()
    (attempt / 'input.json').write_text('owned task', encoding='utf-8')
    first = client(failure=requests.ReadTimeout('Docker stalled'))
    second = client()
    monkeypatch.setattr(runtime, 'client_for', Mock(side_effect=[first, second]))

    def wait(delay):
        first.close.assert_called_once_with()
        assert attempt.is_dir()

    monkeypatch.setattr(runtime.time, 'sleep', wait)
    executor.reconcile()
    assert not attempt.exists()
    second.close.assert_called_once_with()


def test_reconcile_exhaustion_propagates_and_leaves_attempt_files(tmp_path, monkeypatch):
    executor = runtime.DockerExecutor('npipe://test', 'worker-one', root=tmp_path)
    attempt = tmp_path / 'unfinished-attempt'
    attempt.mkdir()
    failures = [requests.ReadTimeout(f'Docker stalled {index}') for index in range(3)]
    connections = [client(failure=failure) for failure in failures]
    factory, sleep = Mock(side_effect=connections), Mock()
    monkeypatch.setattr(runtime, 'client_for', factory)
    monkeypatch.setattr(runtime.time, 'sleep', sleep)
    with pytest.raises(requests.ReadTimeout) as exc:
        executor.reconcile()
    assert exc.value is failures[-1]
    assert factory.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [.2, .4]
    assert attempt.is_dir()
    for connection in connections:
        connection.close.assert_called_once_with()


def test_reconcile_client_creation_transport_failure_is_retried(tmp_path, monkeypatch):
    executor = runtime.DockerExecutor('npipe://test', 'worker-one', root=tmp_path)
    connection = client()
    factory = Mock(side_effect=[requests.ConnectionError('Docker starting'), connection])
    monkeypatch.setattr(runtime, 'client_for', factory)
    monkeypatch.setattr(runtime.time, 'sleep', Mock())
    executor.reconcile()
    assert factory.call_count == 2
    connection.close.assert_called_once_with()


@pytest.mark.parametrize('failure', [docker.errors.APIError('Permission denied', explanation='denied'),
                                   RuntimeError('Unexpected cleanup failure')])
def test_reconcile_permanent_error_propagates_without_retry(tmp_path, monkeypatch, failure):
    executor = runtime.DockerExecutor('npipe://test', 'worker-one', root=tmp_path)
    connection = client(failure=failure)
    factory, sleep = Mock(return_value=connection), Mock()
    monkeypatch.setattr(runtime, 'client_for', factory)
    monkeypatch.setattr(runtime.time, 'sleep', sleep)
    with pytest.raises(type(failure)) as exc:
        executor.reconcile()
    assert exc.value is failure
    factory.assert_called_once_with('npipe://test')
    connection.close.assert_called_once_with()
    sleep.assert_not_called()


def test_reconcile_retries_remove_transport_failure_before_removing_files(tmp_path, monkeypatch):
    executor = runtime.DockerExecutor('unix:///test', 'worker-one', root=tmp_path)
    attempt = tmp_path / 'unfinished-attempt'
    attempt.mkdir()
    owned = Mock()
    owned.remove.side_effect = requests.ReadTimeout('Removal response lost')
    first, second = client(containers=[owned]), client()
    monkeypatch.setattr(runtime, 'client_for', Mock(side_effect=[first, second]))
    monkeypatch.setattr(runtime.time, 'sleep', Mock())
    executor.reconcile()
    assert not attempt.exists()
    owned.remove.assert_called_once_with(force=True)
    first.close.assert_called_once_with()
    second.close.assert_called_once_with()
