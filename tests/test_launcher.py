import os
import io
import json
import socket
import time
from types import SimpleNamespace
import pytest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea
from distributedexec import launcher


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(launcher, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(launcher, 'config_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda _: True)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    (tmp_path / 'config').mkdir()
    window = launcher.Launcher()
    window.interface.setCurrentIndex(window.interface.count() - 1)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        window.port.setValue(probe.getsockname()[1])
    window.show()
    yield app, window
    window.closing = True
    window.worker_command('stop')
    if window.host_process:
        window.action(lambda _: window.host_api('/api/desktop/shutdown'))
    deadline = time.monotonic() + 30
    while (window.host_process or window.worker_process or window.jobs) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.05)
    for process in (window.host_process, window.worker_process):
        if process and process.poll() is None:
            process.terminate(); process.wait(timeout=5)
    window.host_process = window.worker_process = None
    window.close()


def until(app, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate(): return
        time.sleep(.05)
    raise AssertionError('Qt launcher operation timed out')


@pytest.fixture
def isolated_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(launcher, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(launcher, 'config_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(launcher.Launcher, 'check_docker', lambda self: None)
    (tmp_path / 'config').mkdir()
    ui = launcher.Launcher()
    ui.timer.stop()
    yield app, ui
    ui.host_process = ui.worker_process = None
    ui.jobs.clear()
    ui.close()


def test_controls_and_layout_follow_service_readiness(isolated_window):
    app, ui = isolated_window
    assert ui.host_start.isEnabled()
    assert not ui.dashboard_button.isEnabled()
    assert not ui.copy_address_button.isEnabled()
    assert not ui.copy_code_button.isEnabled()
    assert not ui.connect_button.isEnabled()
    assert not ui.pause_button.isEnabled()
    assert not ui.cancel_button.isEnabled()
    assert ui.setup_button.isEnabled()
    ui.set_runtime({'state': 'ready', 'message': 'Runtime ready', 'endpoint': 'unix:///test'})
    assert ui.connect_button.isEnabled()
    assert ui.prepare_button.isEnabled()
    ui.resize(660, 480)
    ui.show()
    app.processEvents()
    scroll = ui.findChild(QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize('routed, expected', [
    ('10.201.244.21', '10.201.244.21'),
    (None, '10.0.0.4'),
    ('169.254.220.107', '10.0.0.4'),
])
def test_default_interface_prefers_active_route_with_usable_fallback(tmp_path, monkeypatch, routed, expected):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(launcher, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(launcher, 'config_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(launcher.Launcher, 'check_docker', lambda self: None)
    (tmp_path / 'config').mkdir()
    addresses = {'Disconnected Ethernet': '169.254.220.107', 'Limited': '169.254.1.2',
        'Ethernet': '10.0.0.4', 'Wi-Fi': '10.201.244.21', 'Loopback': '127.0.0.1'}
    monkeypatch.setattr(launcher.psutil, 'net_if_addrs', lambda: {
        name: [SimpleNamespace(family=socket.AF_INET, address=address)] for name, address in addresses.items()})
    monkeypatch.setattr(launcher.psutil, 'net_if_stats', lambda: {
        name: SimpleNamespace(isup=name != 'Disconnected Ethernet') for name in addresses})
    class RouteSocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect(self, target):
            assert target == ('192.0.2.1', 9)
            if routed is None: raise OSError('No default route')
        def getsockname(self): return routed, 12345
    monkeypatch.setattr(launcher.socket, 'socket', lambda family, kind: RouteSocket())
    ui = launcher.Launcher()
    ui.timer.stop()
    try:
        assert ui.interface.currentData() == expected
        assert ui.interface.findData('169.254.220.107') == -1
        assert ui.interface.itemData(ui.interface.count() - 1) == '127.0.0.1'
    finally:
        ui.close()


def test_expired_code_and_exited_services_disable_controls(isolated_window):
    _, ui = isolated_window
    class Process:
        returncode = 0
        exited = False
        def poll(self): return 0 if self.exited else None
    host, worker = Process(), Process()
    ui.host_process, ui.host_log = host, io.StringIO()
    ui.worker_process, ui.worker_log = worker, io.StringIO()
    ui.pairing = {'code': 'EXPIRED-CODE', 'expires': time.time() - 1}
    ui.update_controls()
    assert ui.dashboard_button.isEnabled()
    assert ui.rotate_button.isEnabled()
    assert not ui.copy_code_button.isEnabled()
    assert not ui.host_start.isEnabled()
    assert not ui.port.isEnabled()
    assert ui.pause_button.isEnabled()
    host.exited = worker.exited = True
    ui.tick()
    assert ui.host_start.isEnabled()
    assert ui.port.isEnabled()
    assert not ui.dashboard_button.isEnabled()
    assert not ui.pause_button.isEnabled()
    assert ui.host_process is ui.worker_process is None


def test_saving_preferences_preserves_worker_from_previous_session(isolated_window, tmp_path):
    _, ui = isolated_window
    profile = tmp_path / 'data' / 'workers' / 'saved-worker' / 'profile.json'
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({'endpoint': 'unix:///test'}), encoding='utf-8')
    ui.preferences().write_text(json.dumps({'worker_profile': str(profile)}), encoding='utf-8')
    ui.restore_preferences()
    ui.workspace_name.setText('another-workspace')
    ui.save_preferences()
    assert json.loads(ui.preferences().read_text(encoding='utf-8'))['worker_profile'] == str(profile)
    assert ui.profile is None
    ui.set_runtime({'state': 'ready', 'message': 'Runtime ready', 'endpoint': 'unix:///test'})
    assert ui.reconnect_button.isEnabled()


@pytest.mark.parametrize('address, code, name, expected', [
    ('http://[broken', 'valid', 'Computer', 'Use a host address'),
    ('http://127.0.0.1:not-a-port', 'valid', 'Computer', 'Use a host address'),
    ('http://127.0.0.1:70000', 'valid', 'Computer', 'Use a host address'),
    ('http://user:password@127.0.0.1:8000', 'valid', 'Computer', 'Use a host address'),
    ('http://127.0.0.1:8000/dashboard', 'valid', 'Computer', 'Use a host address'),
    ('http://127.0.0.1:8000', ' ', 'Computer', 'Enter the pairing code'),
    ('http://127.0.0.1:8000', 'valid', ' ', 'Enter a computer name'),
])
def test_join_rejects_invalid_inputs_before_network(isolated_window, monkeypatch, address, code, name, expected):
    _, ui = isolated_window
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    ui.address.setText(address); ui.code.setText(code); ui.name.setText(name)
    monkeypatch.setattr(ui, 'action', lambda *args, **kwargs: pytest.fail('Invalid input reached the network'))
    ui.join()
    assert expected in ui.messages.toPlainText()
    assert not ui.connecting


def test_join_normalizes_address_and_saves_reconnect_profile(isolated_window, monkeypatch):
    _, ui = isolated_window
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    ui.address.setText('192.168.1.10:8000/'); ui.code.setText('VALID-CODE')
    requests = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'worker_id': 'test-worker', 'credential': 'private-credential'}
    class Process:
        def poll(self): return None
    monkeypatch.setattr(launcher.requests, 'post', lambda address, **kwargs: requests.append((address, kwargs)) or Response())
    monkeypatch.setattr(launcher, 'save', lambda *args: 'test credential store')
    monkeypatch.setattr(ui, 'spawn', lambda *args: (Process(), io.StringIO()))
    monkeypatch.setattr(ui, 'action', lambda callback, done, failed: done(callback(lambda _: None)))
    ui.join()
    assert ui.address.text() == 'http://192.168.1.10:8000'
    assert requests[0][0] == 'http://192.168.1.10:8000/api/pair'
    assert ui.code.text() == ''
    assert not ui.connecting
    assert not ui.connect_button.isEnabled()
    assert ui.pause_button.isEnabled()
    assert json.loads(ui.preferences().read_text(encoding='utf-8'))['worker_profile'] == str(ui.profile)
    assert 'private-credential' not in ui.messages.toPlainText()


@pytest.mark.parametrize('failure, expected', [
    (launcher.requests.Timeout('timeout'), 'did not respond in time'),
    (launcher.requests.ConnectionError('unreachable'), 'Cannot reach the host'),
])
def test_join_reports_actionable_connection_error(isolated_window, monkeypatch, failure, expected):
    _, ui = isolated_window
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    ui.address.setText('http://192.168.1.10:8000'); ui.code.setText('VALID-CODE')
    def post(*args, **kwargs): raise failure
    def action(callback, done, failed):
        try: done(callback(lambda _: None))
        except Exception as exc: failed(str(exc))
    monkeypatch.setattr(launcher.requests, 'post', post)
    monkeypatch.setattr(ui, 'action', action)
    ui.join()
    assert expected in ui.worker_label.text()
    assert ui.connect_button.isEnabled()
    assert not ui.connecting


@pytest.mark.parametrize('detail, expected', [
    ('Invalid or expired pairing code', 'Copy a new code from the host'),
    ('Pairing rate limit; wait one minute', 'Wait one minute before trying again'),
])
def test_join_distinguishes_expired_code_and_pairing_rate_limit(isolated_window, monkeypatch, detail, expected):
    _, ui = isolated_window
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    ui.address.setText('http://192.168.1.10:8000'); ui.code.setText('VALID-CODE')
    response = launcher.requests.Response()
    response.status_code = 409
    response._content = json.dumps({'detail': detail}).encode('utf-8')
    def post(*args, **kwargs): return response
    def action(callback, done, failed):
        try: done(callback(lambda _: None))
        except Exception as exc: failed(str(exc))
    monkeypatch.setattr(launcher.requests, 'post', post)
    monkeypatch.setattr(ui, 'action', action)
    ui.join()
    assert expected in ui.worker_label.text()
    assert ui.connect_button.isEnabled()


def test_compute_setup_available_when_docker_missing_and_cancellable(isolated_window, monkeypatch):
    from distributedexec import setup
    _, ui = isolated_window
    ui.set_runtime({'state': 'missing', 'message': 'Docker missing'})
    pending = []
    monkeypatch.setattr(ui, 'action', lambda callback, done, failed, progress: pending.append((callback, done, failed)))
    monkeypatch.setattr(setup, 'setup_compute', lambda report, cancel: {'state': 'ready', 'message': 'Ready', 'endpoint': 'unix:///test'})
    ui.setup_compute()
    assert ui.setup_running
    assert ui.cancel_button.isEnabled()
    assert not ui.setup_button.isEnabled()
    ui.cancel_setup()
    assert ui.cancel_prepare.is_set()
    assert not ui.cancel_button.isEnabled()
    pending[0][2]('Compute setup cancelled')
    assert not ui.setup_running
    assert ui.setup_button.isEnabled()


@pytest.fixture
def inline_actions(isolated_window, monkeypatch):
    _, ui = isolated_window
    def execute(callback, done=None, failed=None, progress=None):
        try:
            result = callback(progress or (lambda _: None))
        except Exception as exc:
            (failed or ui.message)(str(exc))
        else:
            if done:
                done(result)
    monkeypatch.setattr(ui, 'action', execute)
    return ui


@pytest.mark.parametrize('state, install, login, ready', [
    ('missing', True, False, False),
    ('stopped', False, True, False),
    ('login_required', False, True, False),
    ('ready', False, False, True),
])
def test_cross_network_guidance_and_controls_follow_connection_state(isolated_window, monkeypatch, state, install, login, ready):
    app, ui = isolated_window
    original_lan = ui.interface.currentData()
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('tailscale'))
    ui.set_network({'state': state, 'message': 'Next connection step', 'ipv4': '100.101.102.3' if ready else None,
        'network': 'My private network', 'devices': [{'name': 'Other computer', 'ipv4': '100.101.102.4'}] if ready else []})
    assert not ui.network_box.isHidden()
    assert ui.network_label.text() == 'Next connection step'
    assert ui.network_setup_button.isEnabled() is install
    assert ui.network_login_button.isEnabled() is login
    assert ui.host_start.isEnabled() is ready
    assert not ui.interface.isEnabled()
    assert ui.network_use_peer_button.isEnabled() is ready
    if ready:
        assert ui.interface.currentData() == '100.101.102.3'
        assert 'Other computer' in ui.network_devices.text()
        ui.use_peer_address()
        assert ui.address.text() == 'http://100.101.102.4:8000'
    ui.resize(660, 480); ui.show(); app.processEvents()
    assert ui.findChild(QScrollArea).horizontalScrollBar().maximum() == 0
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('lan'))
    assert ui.host_start.isEnabled()
    assert ui.interface.isEnabled()
    assert ui.interface.currentData() == original_lan


def test_connection_refresh_discovers_late_vpn_without_login_or_browser(inline_actions, monkeypatch):
    ui = inline_actions
    checks = []
    states = iter([
        {'state': 'missing', 'message': 'Install Tailscale'},
        {'state': 'login_required', 'message': 'Sign in to Tailscale'},
        {'state': 'ready', 'message': 'Private connection ready', 'ipv4': '100.90.80.70', 'devices': []},
    ])
    monkeypatch.setattr(launcher, 'check_network', lambda: checks.append(True) or next(states))
    monkeypatch.setattr(launcher, 'login_network', lambda: pytest.fail('Polling attempted to sign in'))
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda _: pytest.fail('Polling opened a browser'))
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('tailscale'))
    assert len(checks) == 1
    assert not ui.host_start.isEnabled()
    ui.network_checked_at = time.monotonic() - 16
    ui.tick()
    assert ui.network_login_button.isEnabled()
    ui.network_checked_at = time.monotonic() - 16
    ui.tick()
    assert ui.host_start.isEnabled()
    assert ui.interface.findData('100.90.80.70') >= 0
    ui.tick(); ui.tick()
    assert len(checks) == 3


def test_network_setup_download_can_be_cancelled_without_starting_services(isolated_window, monkeypatch):
    _, ui = isolated_window
    pending = []
    monkeypatch.setattr(ui, 'action', lambda callback, done, failed, progress: pending.append((callback, done, failed, progress)))
    monkeypatch.setattr(launcher, 'setup_network', lambda report, cancel: pytest.fail('Test must not run an installer'))
    ui.set_network({'state': 'missing', 'message': 'Install Tailscale'})
    ui.setup_network()
    assert ui.network_setting_up
    assert not ui.network_setup_button.isEnabled()
    assert not ui.network_check_button.isEnabled()
    assert ui.network_cancel_button.isEnabled()
    pending[0][3]('Downloading Tailscale…')
    assert 'Downloading' in ui.network_label.text()
    ui.cancel_network_setup()
    assert ui.cancel_network.is_set()
    assert not ui.network_cancel_button.isEnabled()
    pending[0][2]('Tailscale setup cancelled')
    assert not ui.network_setting_up
    assert ui.network_setup_button.isEnabled()
    assert ui.host_process is ui.worker_process is None


def test_network_login_runs_only_when_requested(inline_actions, monkeypatch):
    ui = inline_actions
    logins = []
    state = {'state': 'login_required', 'message': 'Sign in on both computers'}
    monkeypatch.setattr(launcher, 'check_network', lambda: state)
    monkeypatch.setattr(launcher, 'login_network', lambda: logins.append(True) or dict(state, message='Finish browser sign-in'))
    ui.show_network_setup()
    assert logins == []
    assert ui.network_login_button.isEnabled()
    ui.login_network()
    assert logins == [True]
    assert 'Finish browser sign-in' in ui.network_label.text()
    assert not ui.network_checking


@pytest.mark.parametrize('address', ['0.0.0.0', '127.0.0.1', '192.168.1.10', '8.8.8.8', '100.128.0.1', 'invalid'])
def test_across_host_rejects_addresses_outside_approved_vpn(isolated_window, monkeypatch, address):
    _, ui = isolated_window
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    monkeypatch.setattr(ui, 'spawn', lambda *args: pytest.fail('Unapproved interface spawned a host'))
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('tailscale'))
    ui.set_network({'state': 'ready', 'message': 'Invalid VPN adapter', 'ipv4': address})
    assert not ui.host_start.isEnabled()
    ui.start_host()
    assert ui.host_process is None
    assert 'Connect Tailscale' in ui.messages.toPlainText()


def test_across_host_rechecks_and_binds_current_vpn_with_local_dashboard(inline_actions, monkeypatch):
    ui = inline_actions
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('tailscale'))
    ui.set_network({'state': 'ready', 'message': 'VPN ready', 'ipv4': '100.90.80.70'})
    monkeypatch.setattr(launcher, 'check_network', lambda: {'state': 'ready', 'message': 'VPN ready', 'ipv4': '100.90.80.71'})
    monkeypatch.setattr(launcher, 'admin_secret', lambda _: 'test-admin')
    launches = []
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(ui, 'spawn', lambda mode, args: launches.append((mode, args)) or (process, io.StringIO()))
    ui.start_host()
    arguments = launches[0][1]
    assert arguments[arguments.index('--bind') + 1] == '100.90.80.71'
    assert arguments[arguments.index('--address') + 1] == '100.90.80.71'
    assert '0.0.0.0' not in arguments
    assert ui.host_url == f'http://100.90.80.71:{ui.port.value()}'
    assert ui.local_url == f'http://127.0.0.1:{ui.port.value()}'
    assert ui.host_process is process


def test_across_host_does_not_fall_back_to_lan_when_vpn_drops(inline_actions, monkeypatch):
    ui = inline_actions
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('tailscale'))
    ui.set_network({'state': 'ready', 'message': 'VPN ready', 'ipv4': '100.90.80.70'})
    monkeypatch.setattr(launcher, 'check_network', lambda: {'state': 'stopped', 'message': 'Reconnect Tailscale'})
    monkeypatch.setattr(ui, 'spawn', lambda *args: pytest.fail('Disconnected VPN fell back to another interface'))
    ui.start_host()
    assert ui.host_process is None
    assert not ui.starting
    assert not ui.host_start.isEnabled()
    assert 'Reconnect Tailscale' in ui.host_label.text()
    ui.host_mode.setCurrentIndex(ui.host_mode.findData('lan'))
    assert ui.host_start.isEnabled()


def test_connection_invitation_paste_fills_address_and_code_without_saving_secret(isolated_window, monkeypatch):
    _, ui = isolated_window
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    expires = time.time() + 300
    invitation = launcher.encode_invitation('http://100.90.80.70:9000', 'ABCDEF0123', expires)
    ui.invitation.setText(invitation)
    assert ui.address.text() == 'http://100.90.80.70:9000'
    assert ui.code.text() == 'ABCDEF0123'
    assert not ui.network_box.isHidden()
    assert 'Invitation ready' in ui.invitation_label.text()
    ui.set_runtime({'state': 'ready', 'message': 'Runtime ready', 'endpoint': 'unix:///test'})
    assert not ui.connect_button.isEnabled()
    ui.set_network({'state': 'ready', 'message': 'VPN ready', 'ipv4': '100.90.80.71'})
    assert ui.connect_button.isEnabled()
    ui.save_preferences()
    preferences = ui.preferences().read_text(encoding='utf-8')
    assert invitation not in preferences
    assert 'ABCDEF0123' not in preferences


def test_host_copies_a_single_expiring_invitation(isolated_window):
    app, ui = isolated_window
    ui.host_url = 'http://192.168.1.10:8000'
    ui.pairing = {'code': 'ABCDEF0123', 'expires': time.time() + 300}
    ui.host_process = SimpleNamespace(poll=lambda: None)
    ui.update_controls()
    assert ui.copy_invitation_button.isEnabled()
    ui.copy_invitation()
    decoded = launcher.decode_invitation(app.clipboard().text())
    assert decoded == dict(address=ui.host_url, **ui.pairing)
    assert 'ABCDEF0123' not in ui.messages.toPlainText()
    ui.pairing['expires'] = time.time() - 1
    ui.update_controls()
    assert not ui.copy_invitation_button.isEnabled()


def test_invitation_that_expires_after_paste_is_rejected_before_network(isolated_window, monkeypatch):
    _, ui = isolated_window
    now = time.time()
    invitation = launcher.encode_invitation('http://192.168.1.10:8000', 'ABCDEF0123', now + 1)
    ui.invitation.setText(invitation)
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    monkeypatch.setattr(launcher.time, 'time', lambda: now + 2)
    monkeypatch.setattr(ui, 'action', lambda *args, **kwargs: pytest.fail('Expired invitation reached the network'))
    ui.update_controls()
    assert not ui.connect_button.isEnabled()
    ui.join()
    assert 'invitation has expired' in ui.messages.toPlainText()
    assert not ui.connecting


@pytest.mark.parametrize('host', ['worker.my-network.ts.net', 'worker.my-network.tailscale.net', 'worker.my-network.ts.net.'])
def test_tailscale_dns_invitation_opens_connection_setup(isolated_window, monkeypatch, host):
    _, ui = isolated_window
    checks = []
    monkeypatch.setattr(ui, 'check_network', lambda: checks.append(True))
    ui.invitation.setText(launcher.encode_invitation(f'http://{host}:8000', 'ABCDEF0123', time.time() + 300))
    assert not ui.network_box.isHidden()
    assert checks == [True]
    assert ui.address.text() == f'http://{host}:8000'
    ui.set_runtime({'state': 'ready', 'message': 'Compute ready', 'endpoint': 'unix:///test'})
    assert not ui.connect_button.isEnabled()


def test_preferences_restore_across_network_mode_and_fresh_status(isolated_window, monkeypatch):
    _, ui = isolated_window
    checks = []
    monkeypatch.setattr(ui, 'check_network', lambda: checks.append(True))
    ui.preferences().write_text(json.dumps({'host_mode': 'tailscale', 'host_address': 'http://100.90.80.70:8000'}), encoding='utf-8')
    ui.restore_preferences()
    assert ui.host_mode.currentData() == 'tailscale'
    assert not ui.network_box.isHidden()
    assert checks
    assert not ui.host_start.isEnabled()
    assert ui.address.text() == 'http://100.90.80.70:8000'
    ui.save_preferences()
    assert json.loads(ui.preferences().read_text(encoding='utf-8'))['host_mode'] == 'tailscale'


def test_saved_worker_reconnect_checks_saved_hosts_vpn_not_current_address(isolated_window, monkeypatch, tmp_path):
    _, ui = isolated_window
    monkeypatch.setattr(ui, 'check_network', lambda: None)
    profile = tmp_path / 'data' / 'workers' / 'saved-worker' / 'profile.json'
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({'endpoint': 'unix:///test', 'host': 'http://100.90.80.70:8000'}), encoding='utf-8')
    ui.preferences().write_text(json.dumps({'worker_profile': str(profile)}), encoding='utf-8')
    ui.address.setText('http://192.168.1.10:8000')
    ui.runtime = {'state': 'ready', 'endpoint': 'unix:///test'}
    monkeypatch.setattr(ui, 'spawn', lambda *args: pytest.fail('Saved remote worker started without a ready VPN'))
    ui.reconnect()
    assert ui.worker_process is None
    assert not ui.network_box.isHidden()
    assert 'Connect Tailscale before reconnecting' in ui.messages.toPlainText()
    assert 'Enter a current pairing code' not in ui.messages.toPlainText()
    ui.set_network({'state': 'ready', 'message': 'VPN ready', 'ipv4': '100.90.80.71'})
    launches = []
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(ui, 'spawn', lambda mode, args: launches.append((mode, args)) or (process, io.StringIO()))
    ui.reconnect()
    assert launches == [('worker', ['--config', profile])]
    assert ui.worker_process is process


def test_host_without_docker_and_port_conflict(window):
    app, ui = window
    ui.runtime = {'state': 'missing', 'message': 'Docker missing'}
    ui.start_host()
    until(app, lambda: ui.pairing is not None)
    assert 'Hosting at' in ui.host_label.text()
    assert ui.dashboard_button.isEnabled()
    assert not ui.host_start.isEnabled()
    assert ui.status['jobs'] == []
    original = ui.host_process
    # A second service must fail without terminating the first.
    second, output = ui.spawn('host', ['--workspace', ui.workspace, '--bind', '127.0.0.1', '--port', ui.port.value()])
    until(app, lambda: second.poll() is not None)
    output.close()
    assert second.returncode != 0
    assert original.poll() is None
    ui.stop_host()
    until(app, lambda: ui.host_process is None)
    assert ui.host_start.isEnabled()
    assert not ui.dashboard_button.isEnabled()


@pytest.mark.docker
def test_host_and_worker_controls(window):
    app, ui = window
    until(app, lambda: ui.runtime.get('state') == 'ready')
    ui.contribute.setChecked(True)
    ui.start_host()
    until(app, lambda: ui.worker_process is not None and 'connected' in ui.worker_label.text().lower())
    until(app, lambda: any(w['mode'] == 'accepting' for w in ui.status.get('workers', [])))
    ui.worker_command('paused')
    until(app, lambda: any(w['mode'] == 'paused' for w in ui.status.get('workers', [])))
    ui.worker_command('accepting')
    until(app, lambda: any(w['mode'] == 'accepting' for w in ui.status.get('workers', [])))
    ui.worker_command('draining')
    until(app, lambda: ui.worker_process is None)
    ui.reconnect()
    until(app, lambda: ui.worker_process is not None)
    ui.worker_command('stop')
    until(app, lambda: ui.worker_process is None)
