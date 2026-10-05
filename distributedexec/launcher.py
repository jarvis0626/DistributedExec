"""Small native process launcher; the browser remains the computation dashboard."""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit
import psutil
import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QPushButton, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QMessageBox)
from .agent import write_json
from .credentials import admin_secret, save
from .paths import assets, config_dir, data_dir, logs_dir
from .runtime import check_runtime, prepare_runtime


def redacted(value):
    value = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_\-.]+', r'\1[REDACTED]', str(value))
    value = re.sub(r'(?i)((?:credential|token|ticket|pairing.?code|secret|csrf)\s*[=:]\s*)[^\s,}]+', r'\1[REDACTED]', value)
    return value


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Action(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.action(self.signals.progress.emit))
        except Exception as exc:
            self.signals.failed.emit(redacted(exc))


def command(mode, *args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, mode, *map(str, args)]
    return [sys.executable, str(assets() / 'DistributedExec.py'), mode, *map(str, args)]


class Launcher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('DistributedExec')
        self.resize(780, 840)
        self.pool = QThreadPool.globalInstance()
        self.host_process = None
        self.worker_process = None
        self.host_log = self.worker_log = None
        self.workspace = data_dir() / 'workspaces/default'
        self.token = None
        self.host_url = None
        self.profile = None
        self.pairing = None
        self.runtime = {}
        self.setup_running = False
        self.status = {}
        self.polling = False
        self.starting = False
        self.closing = False
        self.cancel_prepare = threading.Event()
        self.close_started = None
        self.jobs = []
        layout = QVBoxLayout(self)
        title = QLabel('DistributedExec')
        title.setStyleSheet('font-size: 25px; font-weight: 600')
        layout.addWidget(title)
        intro = QLabel('Host a workspace, contribute compute, or do both. Your browser is the editing dashboard.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        host_box = QGroupBox('Host a workspace · Docker is optional')
        host_form = QFormLayout(host_box)
        self.interface = QComboBox()
        for name, addresses in psutil.net_if_addrs().items():
            for address in addresses:
                if address.family == socket.AF_INET and address.address != '127.0.0.1':
                    self.interface.addItem(f'{name} · {address.address}', address.address)
        self.interface.addItem('This computer only · 127.0.0.1', '127.0.0.1')
        host_form.addRow('Network interface', self.interface)
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(8000)
        host_form.addRow('Port', self.port)
        self.workspace_name = QLineEdit('default')
        host_form.addRow('Workspace name', self.workspace_name)
        self.host_label = QLabel('Not hosting'); self.host_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_form.addRow(self.host_label)
        self.pair_label = QLabel('Pairing code appears here when hosting')
        self.pair_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_form.addRow(self.pair_label)
        host_buttons = QHBoxLayout()
        self.host_start = self.button(host_buttons, 'Host workspace', self.start_host)
        self.button(host_buttons, 'Open dashboard', self.open_dashboard)
        self.button(host_buttons, 'Copy address', lambda: QApplication.clipboard().setText(self.host_url or ''))
        self.button(host_buttons, 'Copy code', lambda: QApplication.clipboard().setText(self.pairing['code'] if self.pairing else ''))
        self.button(host_buttons, 'Rotate code', self.rotate_code)
        self.button(host_buttons, 'Stop host', self.stop_host)
        host_form.addRow(host_buttons)
        self.revoke_choice = QComboBox()
        revoke_row = QHBoxLayout(); revoke_row.addWidget(self.revoke_choice)
        self.button(revoke_row, 'Revoke worker', self.revoke_worker)
        host_form.addRow('Paired computers', revoke_row)
        self.contribute = QCheckBox('Also contribute compute while hosting (requires ready Docker runtime)')
        self.contribute.toggled.connect(self.toggle_compute)
        host_form.addRow(self.contribute)
        layout.addWidget(host_box)

        runtime_box = QGroupBox('Compute setup')
        runtime_layout = QVBoxLayout(runtime_box)
        self.runtime_label = QLabel('Checking Docker…'); self.runtime_label.setWordWrap(True)
        runtime_layout.addWidget(self.runtime_label)
        self.endpoint_label = QLabel(''); self.endpoint_label.setWordWrap(True)
        runtime_layout.addWidget(self.endpoint_label)
        runtime_buttons = QHBoxLayout()
        self.button(runtime_buttons, 'Check again', self.check_docker)
        self.prepare_button = self.button(runtime_buttons, 'Prepare runtime', self.prepare)
        self.button(runtime_buttons, 'Cancel setup', lambda: self.cancel_prepare.set())
        self.setup_button = self.button(runtime_buttons, 'Set up compute', self.setup_compute)
        self.button(runtime_buttons, 'Setup help', lambda: webbrowser.open('https://docs.docker.com/desktop/setup/install/windows-install/'))
        self.button(runtime_buttons, 'Linux setup', lambda: webbrowser.open('https://docs.docker.com/engine/install/'))
        runtime_layout.addLayout(runtime_buttons)
        setup_hint = QLabel('Set up compute downloads Docker if missing, opens its setup, then prepares the runtime. Existing Docker is reused.')
        setup_hint.setWordWrap(True)
        runtime_layout.addWidget(setup_hint)
        layout.addWidget(runtime_box)

        worker_box = QGroupBox('Join as a worker')
        form = QFormLayout(worker_box)
        self.address = QLineEdit(); self.address.setPlaceholderText('http://192.168.1.10:8000')
        self.code = QLineEdit(); self.code.setPlaceholderText('Expiring pairing code'); self.code.setEchoMode(QLineEdit.Password)
        self.name = QLineEdit(socket.gethostname())
        form.addRow('Host address', self.address); form.addRow('Pairing code', self.code); form.addRow('Computer name', self.name)
        budgets = QHBoxLayout()
        self.cpu = QDoubleSpinBox(); self.cpu.setRange(.1, max(1, os.cpu_count() or 1)); self.cpu.setValue(min(2, os.cpu_count() or 1)); self.cpu.setSingleStep(.5)
        self.memory = QSpinBox(); self.memory.setRange(64, min(131072, psutil.virtual_memory().total // 1048576)); self.memory.setValue(1024)
        self.concurrency = QSpinBox(); self.concurrency.setRange(1, 32); self.concurrency.setValue(1)
        for label, widget in [('CPU', self.cpu), ('RAM MiB', self.memory), ('Concurrency', self.concurrency)]:
            budgets.addWidget(QLabel(label)); budgets.addWidget(widget)
        form.addRow('Resource budgets', budgets)
        worker_buttons = QHBoxLayout()
        self.connect_button = self.button(worker_buttons, 'Connect', self.join)
        self.button(worker_buttons, 'Reconnect saved', self.reconnect)
        self.resume_button = self.button(worker_buttons, 'Resume', lambda: self.worker_command('accepting'))
        self.button(worker_buttons, 'Pause new work', lambda: self.worker_command('paused'))
        self.button(worker_buttons, 'Disconnect after work', lambda: self.worker_command('draining'))
        self.button(worker_buttons, 'Stop now', lambda: self.worker_command('stop'))
        form.addRow(worker_buttons)
        self.worker_label = QLabel('Disconnected'); self.worker_label.setWordWrap(True)
        form.addRow(self.worker_label)
        layout.addWidget(worker_box)
        self.messages = QPlainTextEdit(); self.messages.setReadOnly(True); self.messages.setMaximumBlockCount(100); self.messages.setMaximumHeight(100)
        layout.addWidget(self.messages)
        diagnostics = QHBoxLayout()
        self.button(diagnostics, 'Open logs folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))))
        self.button(diagnostics, 'Copy diagnostics', self.copy_diagnostics)
        layout.addLayout(diagnostics)
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(1000)
        self.restore_preferences()
        self.check_docker()

    def button(self, row, label, callback):
        button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button); return button

    def message(self, value):
        self.messages.appendPlainText(redacted(value))

    def action(self, callback, done=None, failed=None, progress=None):
        task = Action(callback)
        self.jobs.append(task)
        def finished():
            if task in self.jobs: self.jobs.remove(task)
        task.signals.done.connect(done or (lambda _: None))
        task.signals.failed.connect(failed or self.message)
        task.signals.done.connect(finished); task.signals.failed.connect(finished)
        task.signals.progress.connect(progress or self.message)
        self.pool.start(task)

    def preferences(self):
        return config_dir() / 'launcher.json'

    def restore_preferences(self):
        try:
            value = json.loads(self.preferences().read_text(encoding='utf-8'))
            self.address.setText(value.get('host_address', '')); self.name.setText(value.get('name', socket.gethostname()))
            self.cpu.setValue(value.get('cpu', 2)); self.memory.setValue(value.get('memory_mb', 1024)); self.concurrency.setValue(value.get('concurrency', 1))
            self.port.setValue(value.get('port', 8000)); self.workspace_name.setText(value.get('workspace', 'default'))
        except (OSError, ValueError):
            pass

    def save_preferences(self):
        write_json(self.preferences(), {'host_address': self.address.text(), 'name': self.name.text(), 'cpu': self.cpu.value(),
            'memory_mb': self.memory.value(), 'concurrency': self.concurrency.value(), 'port': self.port.value(), 'workspace': self.workspace_name.text(),
            'worker_profile': str(self.profile) if self.profile else None})

    def reconnect(self):
        if self.worker_process or self.runtime.get('state') != 'ready':
            self.message('Prepare the runtime and disconnect any current worker before reconnecting.'); return
        try:
            preferences = json.loads(self.preferences().read_text(encoding='utf-8'))
            profile = Path(preferences['worker_profile']).resolve()
            if not profile.is_relative_to((data_dir() / 'workers').resolve()): raise ValueError('Invalid saved worker path')
            config = json.loads(profile.read_text(encoding='utf-8'))
            if config['endpoint'] != self.runtime['endpoint']: raise ValueError('Selected Docker endpoint changed; select the original context or pair a new worker')
            if self.host_process and not self.contribute.isChecked(): raise ValueError('Enable Also contribute compute while hosting first')
            self.profile = profile
            self.worker_process, self.worker_log = self.spawn('worker', ['--config', profile])
            self.message('Reconnecting saved worker with its original resource budgets. Pair again to change budgets.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.message(f'Cannot reconnect saved worker: {exc}. Enter a current pairing code to connect.')

    def toggle_compute(self, enabled):
        if enabled and self.host_process and self.pairing and not self.worker_process:
            self.join(local=True)
        elif not enabled and self.worker_process and self.profile:
            try:
                config = json.loads(self.profile.read_text(encoding='utf-8'))
                if config['host'] == self.local_url: self.worker_command('draining')
            except (OSError, ValueError, AttributeError): pass

    def spawn(self, mode, arguments):
        output = open(logs_dir() / f'{mode}-process.log', 'a', encoding='utf-8')
        try:
            process = subprocess.Popen(command(mode, *arguments, '--owner-pid', os.getpid()), stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            return process, output
        except Exception:
            output.close(); raise

    def start_host(self):
        if self.host_process and self.host_process.poll() is None: return
        name = self.workspace_name.text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            self.message('Workspace name must contain 1–64 letters, numbers, underscores or hyphens.'); return
        self.workspace = data_dir() / 'workspaces' / name
        ip = self.interface.currentData()
        port = self.port.value()
        self.host_url = f'http://{ip}:{port}'
        self.local_url = f'http://127.0.0.1:{port}'
        self.starting = True; self.host_start.setEnabled(False)
        self.save_preferences()
        def start(_):
            token = admin_secret(self.workspace)
            process, output = self.spawn('host', ['--workspace', self.workspace, '--bind', ip, '--address', ip, '--port', port])
            return token, process, output
        def started(value):
            self.token, self.host_process, self.host_log = value
            self.host_label.setText('Starting coordinator…')
        self.action(start, started, self.host_start_failed)

    def host_start_failed(self, error):
        self.starting = False; self.host_start.setEnabled(True); self.message(error)

    def host_api(self, path, value=None, method='POST'):
        response = requests.request(method, self.local_url + path, json=value,
            headers={'Authorization': 'Bearer ' + self.token}, timeout=(1, 3))
        response.raise_for_status(); return response.json()

    def rotate_code(self):
        if not self.token or not self.host_process or self.host_process.poll() is not None: return
        self.action(lambda _: self.host_api('/api/desktop/pairing'), self.set_pairing)

    def set_pairing(self, value):
        self.pairing = value
        self.pair_label.setText(f'Pairing code: {value["code"]} · expires in {max(0, int(value["expires"] - time.time()))}s')

    def open_dashboard(self):
        if not self.token or not self.host_process or self.host_process.poll() is not None:
            self.message('Host a workspace first.'); return
        self.action(lambda _: self.host_api('/api/desktop/ticket'), lambda value: webbrowser.open(self.local_url + '/#ticket=' + value['ticket']))

    def join(self, checked=False, local=False):
        if self.worker_process and self.worker_process.poll() is None:
            self.message('A worker is already connected. Use Pause, Disconnect or Stop now.'); return
        if self.runtime.get('state') != 'ready':
            self.message('Prepare the runtime before contributing compute. Hosting remains available.'); return
        if not local and self.host_process and self.host_process.poll() is None and not self.contribute.isChecked():
            self.message('Enable Also contribute compute while hosting to run both roles.'); return
        host = self.local_url if local else self.address.text().strip().rstrip('/')
        parsed = urlsplit(host)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            self.message('Use a host address such as http://192.168.1.10:8000 without a path or credentials.'); return
        payload = {'code': self.pairing['code'] if local else self.code.text().strip(), 'name': self.name.text().strip(),
            'cpu': self.cpu.value(), 'memory_mb': self.memory.value(), 'concurrency': self.concurrency.value()}
        if payload['cpu'] > self.runtime.get('daemon_cpu', 128) or payload['memory_mb'] > self.runtime.get('daemon_memory_mb', 131072):
            self.message('Worker budget exceeds resources allocated to the Docker daemon. Adjust the budget or Docker Desktop resources.'); return
        self.connect_button.setEnabled(False); self.save_preferences()
        endpoint = self.runtime['endpoint']
        def connect(_):
            response = requests.post(host + '/api/pair', json=payload, timeout=(3, 5)); response.raise_for_status()
            pair = response.json()
            reference = 'worker:' + pair['worker_id']
            location = save(reference, pair['credential'])
            directory = data_dir() / 'workers' / pair['worker_id']
            directory.mkdir(parents=True, exist_ok=True)
            profile = directory / 'profile.json'
            write_json(profile, {'host': host, 'worker_id': pair['worker_id'], 'credential_ref': reference, 'endpoint': endpoint,
                'service_dir': str(directory), 'concurrency': payload['concurrency']})
            process, output = self.spawn('worker', ['--config', profile])
            return profile, process, output, location
        def connected(value):
            self.profile, self.worker_process, self.worker_log, location = value
            self.code.clear(); self.connect_button.setEnabled(True); self.save_preferences(); self.message(f'Paired. Credential stored in {location}.')
        def failed(error):
            self.connect_button.setEnabled(True); self.message(error)
        self.action(connect, connected, failed)

    def worker_command(self, command):
        if self.profile and self.worker_process and self.worker_process.poll() is None:
            write_json(self.profile.parent / 'command.json', {'command': command})
        else:
            self.message('Connect a worker first.')

    def revoke_worker(self):
        worker = self.revoke_choice.currentData()
        if worker and self.token:
            self.action(lambda _: self.host_api(f'/api/workers/{worker}/revoke'), lambda _: self.message('Worker credential revoked. Its running attempts will expire and be reassigned where eligible.'))

    def check_docker(self):
        self.action(lambda _: check_runtime(), self.set_runtime)

    def set_runtime(self, value):
        self.runtime = value
        if self.setup_running:
            return
        self.runtime_label.setText(f'{value["state"].capitalize()}: {value["message"]}')
        self.endpoint_label.setText(f'Selected context: {value.get("context", "unavailable")} · endpoint: {value.get("endpoint", "unavailable")}')

    def prepare(self):
        if self.setup_running:
            return
        if not self.runtime.get('endpoint'):
            self.message('Start Docker Desktop in Linux-container mode, then Check again.'); return
        self.cancel_prepare.clear(); self.prepare_button.setEnabled(False)
        self.runtime_label.setText('Preparing runtime…')
        def finished(_):
            self.prepare_button.setEnabled(True); self.check_docker()
        def failed(error):
            self.prepare_button.setEnabled(True); self.runtime_label.setText('Failed: ' + error); self.message(error)
        self.action(lambda progress: prepare_runtime(self.runtime['endpoint'], progress, self.cancel_prepare), finished, failed)

    def setup_compute(self):
        if self.setup_running or not self.prepare_button.isEnabled():
            return
        from .setup import setup_compute
        self.setup_running = True
        self.cancel_prepare.clear()
        self.setup_button.setEnabled(False); self.prepare_button.setEnabled(False)
        def restore():
            self.setup_running = False
            self.setup_button.setEnabled(True); self.prepare_button.setEnabled(True)
        def finished(value):
            restore(); self.set_runtime(value); self.message('Compute setup complete.')
        def failed(error):
            restore(); self.runtime_label.setText(error); self.message(error)
        def progress(value):
            self.runtime_label.setText(value.strip()); self.message(value)
        self.action(lambda report: setup_compute(report, self.cancel_prepare), finished, failed, progress)

    def tick(self):
        for mode in ('host', 'worker'):
            process = getattr(self, mode + '_process')
            if process and process.poll() is not None:
                getattr(self, mode + '_log').close()
                setattr(self, mode + '_process', None)
                if mode == 'host':
                    self.host_label.setText('Not hosting'); self.pair_label.setText('Pairing unavailable'); self.pairing = None
                    self.starting = False; self.host_start.setEnabled(True)
                else:
                    self.worker_label.setText('Disconnected')
                    if self.profile:
                        try:
                            state = json.loads((self.profile.parent / 'state.json').read_text(encoding='utf-8'))
                            if state.get('error'):
                                self.worker_label.setText('Disconnected: ' + redacted(state['error']))
                                self.message(state['error'])
                        except (OSError, ValueError): pass
                if process.returncode:
                    path = logs_dir() / f'{mode}.log'
                    self.message(f'{mode.capitalize()} stopped with an error. ' + (path.read_text(encoding='utf-8')[-1500:] if path.exists() else 'Open logs for details.'))
        if self.pairing:
            seconds = max(0, int(self.pairing['expires'] - time.time()))
            self.pair_label.setText(f'Pairing code: {self.pairing["code"]} · {seconds}s remaining' if seconds else 'Pairing code expired. Select Rotate code.')
        if self.profile and self.worker_process:
            try:
                state = json.loads((self.profile.parent / 'state.json').read_text(encoding='utf-8'))
                self.worker_label.setText(f'{state["connection"]} · {state["mode"]} · {len(state["active"])} active attempts\nReserved {state["reserved_cpu"]} CPU / {state["reserved_memory_mb"]} MiB RAM (not measured usage)' + ('\n' + redacted(state['error']) if state.get('error') else ''))
            except (OSError, ValueError): pass
        if self.host_process and self.token and not self.polling:
            self.polling = True
            def received(state):
                self.polling = False; self.status = state
                self.host_label.setText(f'Hosting at {self.host_url}')
                selected = self.revoke_choice.currentData(); self.revoke_choice.clear()
                for worker in state['workers']:
                    if not worker['revoked']: self.revoke_choice.addItem(worker['name'], worker['id'])
                self.revoke_choice.setCurrentIndex(max(0, self.revoke_choice.findData(selected)))
                if self.starting:
                    self.starting = False; self.host_start.setEnabled(True)
                    def paired(value):
                        self.set_pairing(value); self.open_dashboard()
                        if self.contribute.isChecked(): self.join(local=True)
                    self.action(lambda _: self.host_api('/api/desktop/pairing'), paired)
            def failed(_): self.polling = False
            self.action(lambda _: self.host_api('/api/status', method='GET'), received, failed)
        if self.closing and not self.host_process and not self.worker_process and not self.jobs:
            self.close()
        elif self.closing and self.close_started and time.monotonic() - self.close_started > 35:
            for process in (self.host_process, self.worker_process):
                if process and process.poll() is None: process.terminate()
            self.close_started = None
            if self.profile:
                from .runtime import DockerExecutor
                config = json.loads(self.profile.read_text(encoding='utf-8'))
                self.action(lambda _: DockerExecutor(config['endpoint'], config['worker_id']).reconcile())
            self.message('An owned service did not exit in time and was stopped. Reconciling its containers; see logs for errors.')

    def stop_host(self):
        if not self.host_process: return
        active = self.status.get('active_jobs', 0)
        if active and QMessageBox.question(self, 'Stop coordinator?', f'{active} jobs are queued or running. Workers will stop when their leases cannot be renewed. Jobs and cancellation history persist and eligible chunks can retry after restart. Stop hosting?') != QMessageBox.Yes:
            return
        self.action(lambda _: self.host_api('/api/desktop/shutdown'), lambda _: self.message('Coordinator shutting down; durable history is preserved.'))

    def copy_diagnostics(self):
        from . import __version__
        details = {'application': 'DistributedExec', 'version': __version__, 'platform': sys.platform,
            'frozen': bool(getattr(sys, 'frozen', False)), 'runtime': self.runtime, 'hosting': bool(self.host_process),
            'worker_running': bool(self.worker_process), 'logs_directory': str(logs_dir())}
        QApplication.clipboard().setText(redacted(json.dumps(details, indent=2)))
        self.message('Diagnostics copied; credentials and pairing codes are excluded.')

    def closeEvent(self, event):
        if not self.host_process and not self.worker_process and not self.jobs:
            self.save_preferences(); event.accept(); return
        event.ignore()
        if self.closing: return
        if QMessageBox.question(self, 'Close DistributedExec?', 'Close the launcher and stop its services? Running worker containers will be interrupted; queued jobs and history remain in the workspace. Stop and close?') != QMessageBox.Yes:
            return
        self.closing = True; self.cancel_prepare.set(); self.worker_command('stop')
        self.close_started = time.monotonic()
        if self.host_process:
            self.action(lambda _: self.host_api('/api/desktop/shutdown'))
        self.message('Stopping owned services and containers…')


def launch(setup_compute=False):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('DistributedExec')
    window = Launcher(); window.show()
    if setup_compute:
        QTimer.singleShot(250, window.setup_compute)
    return app.exec()
