import base64
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import Mock

import pytest

from distributedexec import network


def status(**overrides):
    return {'BackendState': 'Running', 'TailscaleIPs': ['fd7a:115c:a1e0::1', '100.70.1.1'],
            'Self': {'Online': True}, 'CurrentTailnet': {'MagicDNSSuffix': 'tail123.ts.net'},
            **overrides}


def cli(monkeypatch, value):
    monkeypatch.setattr(network, 'tailscale_executable', lambda: Path('tailscale.exe'))
    run = Mock(return_value=Mock(returncode=0, stdout=json.dumps(value), stderr=''))
    monkeypatch.setattr(network.subprocess, 'run', run)
    return run


def raw_invitation(value):
    return 'DE1.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def test_cli_discovery_uses_path_then_install_directory(tmp_path, monkeypatch):
    installed = tmp_path / 'Tailscale' / 'tailscale.exe'
    installed.parent.mkdir()
    installed.write_bytes(b'fixture')
    monkeypatch.setattr(network.shutil, 'which', lambda _: 'C:/tools/tailscale.exe')
    monkeypatch.setenv('ProgramW6432', str(tmp_path))
    assert network.tailscale_executable() == Path('C:/tools/tailscale.exe')
    monkeypatch.setattr(network.shutil, 'which', lambda _: None)
    assert network.tailscale_executable() == installed


def test_missing_cli_never_runs_a_process_or_browser(monkeypatch):
    monkeypatch.setattr(network, 'tailscale_executable', lambda: None)
    run, browser = Mock(), Mock()
    monkeypatch.setattr(network.subprocess, 'run', run)
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    assert network.check_network()['state'] == 'missing'
    assert network.login_network()['state'] == 'missing'
    run.assert_not_called()
    browser.assert_not_called()


def test_ready_status_returns_only_safe_online_devices(monkeypatch):
    value = status(AuthURL='https://login.tailscale.com/a/SECRET', User={'1': {'LoginName': 'private@example.com'}},
                   Peer={'one': {'Online': True, 'TailscaleIPs': ['100.70.1.2'],
                                 'DNSName': 'worker.tail123.ts.net.', 'HostName': '<b>Worker</b>\n',
                                 'PublicKey': 'SECRET', 'CurAddr': '203.0.113.2'},
                         'two': {'Online': False, 'TailscaleIPs': ['100.70.1.3'], 'HostName': 'Offline'},
                         'three': {'Online': True, 'TailscaleIPs': ['8.8.8.8']},
                         'four': None})
    run = cli(monkeypatch, value)
    browser = Mock()
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    result = network.check_network()
    assert result['state'] == 'ready'
    assert result['ipv4'] == '100.70.1.1'
    assert result['network'] == 'tail123.ts.net'
    assert result['devices'] == [{'name': 'bWorkerb', 'ipv4': '100.70.1.2',
                                 'dns_name': 'worker.tail123.ts.net'}]
    serialized = json.dumps(result)
    assert 'SECRET' not in serialized and 'private@example.com' not in serialized
    assert '203.0.113.2' not in serialized
    assert run.call_args.args[0] == ['tailscale.exe', 'status', '--json']
    assert run.call_args.kwargs['timeout'] == network.STATUS_TIMEOUT
    assert not run.call_args.kwargs.get('shell')
    browser.assert_not_called()


@pytest.mark.parametrize('backend, expected', [('NeedsLogin', 'login_required'),
    ('NeedsMachineAuth', 'login_required'), ('Stopped', 'stopped'), ('Starting', 'stopped'),
    ('NoState', 'stopped')])
def test_status_states_are_actionable(monkeypatch, backend, expected):
    cli(monkeypatch, status(BackendState=backend))
    result = network.check_network()
    assert result['state'] == expected
    assert result['ipv4'] is None and not result['devices']


@pytest.mark.parametrize('changes', [{'TailscaleIPs': []}, {'TailscaleIPs': ['203.0.113.1']},
                                    {'Self': {'Online': False}}, {'TailscaleIPs': ['invalid']}])
def test_ready_requires_a_usable_vpn_ipv4(monkeypatch, changes):
    cli(monkeypatch, status(**changes))
    assert network.check_network()['state'] == 'stopped'


@pytest.mark.parametrize('failure', [OSError('private error'), subprocess.TimeoutExpired('status', 5),
                                   ValueError('private error')])
def test_status_failure_is_bounded_and_does_not_leak_output(monkeypatch, failure):
    run = cli(monkeypatch, status())
    run.side_effect = failure
    result = network.check_network()
    assert result['state'] == 'stopped'
    assert 'private error' not in result['message']


def test_invalid_status_does_not_leak_auth_data(monkeypatch):
    run = cli(monkeypatch, status())
    for output in ('bad SECRET', '[]', 'null'):
        run.return_value.stdout = output
        assert network.check_network()['state'] == 'stopped'


class Response:
    url = 'https://pkgs.tailscale.com/stable/tailscale-setup-1.102.4.exe'
    headers = {'Content-Length': '4'}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b'test'


def test_installer_cache_is_reverified_and_no_partial_remains(tmp_path, monkeypatch):
    fetch, verify = Mock(return_value=Response()), Mock()
    monkeypatch.setattr(network.requests, 'get', fetch)
    monkeypatch.setattr(network, 'verify_installer', verify)
    target = network.download_installer(root=tmp_path)
    assert target.read_bytes() == b'test'
    assert network.download_installer(root=tmp_path) == target
    assert fetch.call_count == 1
    assert verify.call_count == 2
    assert not list(tmp_path.glob('*.part'))
    assert fetch.call_args.args[0] == network.INSTALLER_URL


@pytest.mark.parametrize('reason', ['unsigned', 'truncated', 'cancelled', 'redirect', 'insecure',
                                   'userinfo', 'too_large', 'growing', 'empty'])
def test_failed_installer_never_leaves_an_executable(tmp_path, monkeypatch, reason):
    response = Response()
    cancel = threading.Event()
    if reason == 'truncated': response.headers = {'Content-Length': '8'}
    if reason == 'redirect': response.url = 'https://example.com/setup.exe'
    if reason == 'insecure': response.url = 'http://pkgs.tailscale.com/setup.exe'
    if reason == 'userinfo': response.url = 'https://user@pkgs.tailscale.com/setup.exe'
    if reason == 'too_large': response.headers = {'Content-Length': str(network.MAX_INSTALLER + 1)}
    if reason == 'growing': monkeypatch.setattr(network, 'MAX_INSTALLER', 3)
    if reason == 'empty': response.iter_content = lambda _: []
    if reason == 'cancelled': cancel.set()
    monkeypatch.setattr(network.requests, 'get', Mock(return_value=response))
    monkeypatch.setattr(network, 'verify_installer', Mock(
        side_effect=RuntimeError('Invalid signature') if reason == 'unsigned' else None))
    with pytest.raises(RuntimeError):
        network.download_installer(cancel=cancel, root=tmp_path)
    assert not list(tmp_path.glob('*.exe'))
    assert not list(tmp_path.glob('*.part'))


def test_cancellation_during_download_removes_partial(tmp_path, monkeypatch):
    stop = threading.Event()
    response = Response()

    def blocks(size):
        yield b'te'
        stop.set()
        yield b'st'

    response.iter_content = blocks
    monkeypatch.setattr(network.requests, 'get', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='cancelled'):
        network.download_installer(cancel=stop, root=tmp_path)
    assert not list(tmp_path.glob('*.exe')) and not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('status_value,subject,accepted', [
    ('Valid', 'CN=Tailscale Inc., O=Tailscale Inc., C=US', True),
    ('Valid', 'CN=Tailscale Inc, O=Tailscale Inc, C=US', True),
    ('NotSigned', 'CN=Tailscale Inc., O=Tailscale Inc., C=US', False),
    ('Valid', 'CN=Other, O=Tailscale Inc. Imposter, C=US', False),
])
def test_signature_requires_exact_trusted_publisher(tmp_path, monkeypatch, status_value, subject, accepted):
    run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({'status': status_value, 'subject': subject})))
    monkeypatch.setattr(network.subprocess, 'run', run)
    path = tmp_path / "Tailscale $setup's file.exe"
    if accepted:
        network.verify_installer(path)
    else:
        with pytest.raises(RuntimeError): network.verify_installer(path)
    assert run.call_args.kwargs['env']['DISTRIBUTEDEXEC_VERIFY_FILE'] == str(path.resolve())
    assert str(path) not in run.call_args.args[0][-1]


def test_setup_opens_verified_vendor_wizard_without_install_flags(monkeypatch):
    monkeypatch.setattr(network.sys, 'platform', 'win32')
    monkeypatch.setattr(network, 'check_network', lambda: {'state': 'missing'})
    download = Mock(return_value=Path('Tailscale Setup.exe'))
    launch = Mock()
    monkeypatch.setattr(network, 'download_installer', download)
    monkeypatch.setattr(network.os, 'startfile', launch, raising=False)
    progress, cancel = Mock(), threading.Event()
    result = network.setup_network(progress, cancel)
    assert result['state'] == 'missing'
    assert 'Finish installation' in result['message']
    download.assert_called_once_with(progress, cancel)
    launch.assert_called_once_with('Tailscale Setup.exe', 'open')


def test_setup_ready_preserves_existing_account_and_never_downloads(monkeypatch):
    ready = {'state': 'ready', 'ipv4': '100.70.1.1'}
    monkeypatch.setattr(network, 'check_network', lambda: ready)
    download, login = Mock(), Mock()
    monkeypatch.setattr(network, 'download_installer', download)
    monkeypatch.setattr(network, 'login_network', login)
    assert network.setup_network() is ready
    download.assert_not_called()
    login.assert_not_called()


def test_setup_installed_uses_current_client_instead_of_reinstalling(monkeypatch):
    monkeypatch.setattr(network, 'check_network', lambda: {'state': 'login_required'})
    login = Mock(return_value={'state': 'login_required', 'message': 'Finish sign-in'})
    monkeypatch.setattr(network, 'login_network', login)
    download = Mock()
    monkeypatch.setattr(network, 'download_installer', download)
    assert network.setup_network() == login.return_value
    login.assert_called_once_with()
    download.assert_not_called()


def test_setup_cancelled_before_launch_never_mutates_system(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    check = Mock()
    monkeypatch.setattr(network, 'check_network', check)
    with pytest.raises(RuntimeError, match='cancelled'):
        network.setup_network(cancel=cancel)
    check.assert_not_called()


def test_login_ready_is_read_only(monkeypatch):
    run = cli(monkeypatch, status())
    browser = Mock()
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    assert network.login_network()['state'] == 'ready'
    assert run.call_count == 1
    browser.assert_not_called()


@pytest.mark.parametrize('timed_out', [False, True])
def test_login_preserves_profile_and_privately_opens_vendor_url(monkeypatch, timed_out):
    run = cli(monkeypatch, status(BackendState='NeedsLogin'))
    sign_in = 'https://login.tailscale.com/a/0123456789abcdef'
    initial = run.return_value
    output = '\nTo authenticate, visit:\n\n\t' + sign_in + '\n'
    final = subprocess.TimeoutExpired('tailscale up', 8, stderr=output.encode()) if timed_out else \
        Mock(returncode=0, stdout='', stderr=output)
    run.side_effect = [initial, final]
    browser = Mock(return_value=True)
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    result = network.login_network()
    browser.assert_called_once_with(sign_in)
    assert result['state'] == 'login_required'
    assert sign_in not in json.dumps(result)
    assert run.call_args.args[0] == ['tailscale.exe', 'up']
    assert run.call_args.kwargs['timeout'] == network.LOGIN_TIMEOUT


@pytest.mark.parametrize('url', ['http://login.tailscale.com/a/123', 'https://evil.com/a/123',
    'https://login.tailscale.com.evil.com/a/123', 'https://user@login.tailscale.com/a/123',
    'https://login.tailscale.com:443/a/123', 'https://login.tailscale.com/a/123?secret=yes',
    'https://login.tailscale.com/a/123#secret', 'https://login.tailscale.com/other'])
def test_login_refuses_untrusted_auth_links(monkeypatch, url):
    initial_status = status(BackendState='NeedsLogin', AuthURL=url)
    run = cli(monkeypatch, initial_status)
    initial = run.return_value
    run.side_effect = [initial, Mock(returncode=1, stdout='', stderr=url), initial]
    browser = Mock()
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    assert network.login_network()['state'] == 'login_required'
    browser.assert_not_called()


def test_approval_opens_admin_console_without_switching_profile(monkeypatch):
    run = cli(monkeypatch, status(BackendState='NeedsMachineAuth'))
    browser = Mock()
    monkeypatch.setattr(network.webbrowser, 'open', browser)
    assert network.login_network()['state'] == 'login_required'
    browser.assert_called_once_with('https://login.tailscale.com/admin/machines')
    assert run.call_count == 1


@pytest.mark.parametrize('address', ['http://100.70.1.1:8000', 'http://192.168.1.5:8000',
                                   'https://worker.tail123.ts.net:443', 'http://127.0.0.1:8000'])
def test_invitation_round_trip_has_only_short_lived_pairing_data(address):
    encoded = network.encode_invitation(address, 'ABC0123456', 1600, now=1000)
    assert encoded.startswith('DE1.')
    assert network.decode_invitation(' \n' + encoded + '\n ', now=1000) == {
        'address': address, 'code': 'ABC0123456', 'expires': 1600}
    assert len(encoded) < 250


@pytest.mark.parametrize('address', ['ftp://100.70.1.1:8000', 'http://8.8.8.8:8000',
    'http://0.0.0.0:8000', 'http://169.254.0.5:8000', 'http://203.0.113.1:8000',
    'http://host.example:8000', 'http://user:pass@100.70.1.1:8000', 'http://100.70.1.1:8000/path',
    'http://100.70.1.1:8000?token=1', 'http://100.70.1.1:8000#secret', 'http://100.70.1.1:0',
    'http://100.70.1.1:65536', 'http://100.70.1.1:bad', 'http://100.70.1.1:', 'http://[::1]:8000',
    'http://100.70.1.1\\@evil.com', 'http://100.70.1.1:\n8000', 'http://ts.net',
    'http://bad_.tail123.ts.net'])
def test_invitation_rejects_unsafe_host_urls(address):
    value = {'address': address, 'code': 'ABC0123456', 'expires': 1200}
    with pytest.raises(ValueError): network.decode_invitation(raw_invitation(value), now=1000)
    with pytest.raises(ValueError): network.encode_invitation(address, 'ABC0123456', 1200, now=1000)


@pytest.mark.parametrize('expires', [999, 1000, 1661, True, '1200', None, float('nan'), float('inf')])
def test_invitation_requires_an_unexpired_bounded_timestamp(expires):
    value = {'address': 'http://100.70.1.1:8000', 'code': 'ABC0123456', 'expires': expires}
    with pytest.raises(ValueError): network.decode_invitation(raw_invitation(value), now=1000)


def test_invitation_allows_receiver_clock_behind_without_extending_host_code():
    invitation = network.encode_invitation('http://100.70.1.1:8000', 'ABC0123456', 1600, now=1000)
    assert network.decode_invitation(invitation, now=940)['expires'] == 1600
    with pytest.raises(ValueError): network.decode_invitation(invitation, now=939)
    with pytest.raises(ValueError): network.decode_invitation(invitation, now=1600)
    with pytest.raises(ValueError):
        network.encode_invitation('http://100.70.1.1:8000', 'ABC0123456', 1601, now=1000)


@pytest.mark.parametrize('code', ['saved-worker-credential', '1234', 'ZZZ0123456', '', True, None])
def test_invitation_cannot_carry_a_worker_credential(code):
    value = {'address': 'http://100.70.1.1:8000', 'code': code, 'expires': 1200}
    with pytest.raises(ValueError): network.decode_invitation(raw_invitation(value), now=1000)


@pytest.mark.parametrize('value', ['DE2.abc', 'DE1.', 'DE1.!', 'DE1.a', 'DE1.' + 'a' * 1025,
                                  'arbitrary text', None])
def test_invitation_rejects_damaged_or_unknown_format(value):
    with pytest.raises(ValueError): network.decode_invitation(value, now=1000)


def test_invitation_rejects_extra_fields_and_duplicates():
    value = {'address': 'http://100.70.1.1:8000', 'code': 'ABC0123456', 'expires': 1200,
             'credential': 'long-lived-secret'}
    with pytest.raises(ValueError): network.decode_invitation(raw_invitation(value), now=1000)
    payload = b'{"address":"http://100.70.1.1:8000","code":"ABC0123456","expires":1200,"expires":1300}'
    encoded = 'DE1.' + base64.urlsafe_b64encode(payload).decode().rstrip('=')
    with pytest.raises(ValueError): network.decode_invitation(encoded, now=1000)
