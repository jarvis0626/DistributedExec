import subprocess
import threading
from pathlib import Path
from unittest.mock import Mock
import pytest
from distributedexec import setup


class Response:
    url = setup.DOCKER_URL
    headers = {'Content-Length': '4'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b'test'


def test_signed_download_cached_and_partial_removed(tmp_path, monkeypatch):
    fetch, verify = Mock(return_value=Response()), Mock()
    monkeypatch.setattr(setup.requests, 'get', fetch)
    monkeypatch.setattr(setup, 'verify_installer', verify)
    stop = threading.Event()
    target = setup.download_installer(lambda _: None, stop, tmp_path)
    assert target.read_bytes() == b'test'
    assert not list(tmp_path.glob('*.part'))
    assert setup.download_installer(lambda _: None, stop, tmp_path) == target
    assert fetch.call_count == 1
    assert verify.call_count == 2


@pytest.mark.parametrize('reason', ['unsigned', 'truncated', 'cancelled', 'redirect'])
def test_failed_download_is_never_retained(tmp_path, monkeypatch, reason):
    response = Response()
    stop = threading.Event()
    if reason == 'truncated': response.headers = {'Content-Length': '8'}
    if reason == 'redirect': response.url = 'https://untrusted.example/setup.exe'
    if reason == 'cancelled': stop.set()
    monkeypatch.setattr(setup.requests, 'get', Mock(return_value=response))
    monkeypatch.setattr(setup, 'verify_installer', Mock(side_effect=RuntimeError('Invalid signature')) if reason == 'unsigned' else Mock())
    with pytest.raises(RuntimeError): setup.download_installer(lambda _: None, stop, tmp_path)
    assert not list(tmp_path.glob('*.exe'))
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('status,subject,accepted', [
    ('Valid', 'CN=Docker Inc, O=Docker Inc, C=US', True),
    ('NotSigned', 'CN=Docker Inc, O=Docker Inc, C=US', False),
    ('Valid', 'CN=Other, O=Docker Inc Imposter, C=US', False),
])
def test_signature_requires_valid_docker_publisher(tmp_path, monkeypatch, status, subject, accepted):
    import json
    process = Mock(returncode=0, stdout=json.dumps({'status': status, 'subject': subject}))
    run = Mock(return_value=process)
    monkeypatch.setattr(setup.subprocess, 'run', run)
    monkeypatch.setattr(setup.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    path = tmp_path / "Docker $installer's file.exe"
    if accepted: setup.verify_installer(path)
    else:
        with pytest.raises(RuntimeError): setup.verify_installer(path)
    assert str(path.resolve()) == run.call_args.kwargs['env']['DISTRIBUTEDEXEC_VERIFY_FILE']
    assert str(path) not in run.call_args.args[0][-1]


def test_ready_docker_does_not_download_or_reinstall(monkeypatch):
    monkeypatch.setattr(setup.os, 'name', 'nt')
    ready = {'state': 'ready'}
    monkeypatch.setattr(setup, 'check_runtime', lambda: ready)
    download = Mock(side_effect=AssertionError('Unexpected download'))
    monkeypatch.setattr(setup, 'download_installer', download)
    monkeypatch.setattr(setup.subprocess, 'Popen', Mock(side_effect=AssertionError('Unexpected install')))
    assert setup.setup_compute(lambda _: None, threading.Event()) == ready


def test_existing_stopped_docker_is_started_then_prepared(monkeypatch):
    monkeypatch.setattr(setup.os, 'name', 'nt')
    monkeypatch.setattr(setup, 'desktop_executable', lambda: Path('Docker Desktop.exe'))
    monkeypatch.setattr(setup, 'check_runtime', Mock(side_effect=[{'state': 'installed but stopped'},
        {'state': 'preparing runtime', 'endpoint': 'npipe://local'}, {'state': 'ready'}]))
    monkeypatch.setattr(setup, 'download_installer', Mock(side_effect=AssertionError('Unexpected download')))
    launch, prepare = Mock(), Mock()
    monkeypatch.setattr(setup.subprocess, 'Popen', launch)
    monkeypatch.setattr(setup, 'prepare_runtime', prepare)
    assert setup.setup_compute(lambda _: None, threading.Event())['state'] == 'ready'
    launch.assert_called_once_with(['Docker Desktop.exe'])
    assert prepare.call_args.args[0] == 'npipe://local'
