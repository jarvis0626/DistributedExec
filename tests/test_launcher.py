import os
import socket
import time
import pytest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox
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


def test_host_without_docker_and_port_conflict(window):
    app, ui = window
    ui.runtime = {'state': 'missing', 'message': 'Docker missing'}
    ui.start_host()
    until(app, lambda: ui.pairing is not None)
    assert 'Hosting at' in ui.host_label.text()
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
