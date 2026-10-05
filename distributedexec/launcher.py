"""Small native process launcher; the browser remains the computation dashboard."""
import json
import ipaddress
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
    QPushButton, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit, QMessageBox,
    QScrollArea)
from .agent import write_json
from .credentials import admin_secret, save
from .paths import assets, config_dir, data_dir, logs_dir
from .runtime import check_runtime, prepare_runtime
from .network import check_network, setup_network, login_network, encode_invitation, decode_invitation


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


def network_ipv4(value):
    """Across-network hosting can only use an approved Tailscale IPv4 address."""
    try:
        address = ipaddress.ip_address(value.get('ipv4') or '')
        if value.get('state') == 'ready' and address in ipaddress.ip_network('100.64.0.0/10'):
            return str(address)
    except ValueError:
        pass
    return None


def tailscale_address(value):
    try:
        host = (urlsplit(value if '://' in value else 'http://' + value).hostname or '').rstrip('.')
        if host.endswith(('.ts.net', '.tailscale.net')):
            return True
        return ipaddress.ip_address(host) in ipaddress.ip_network('100.64.0.0/10')
    except ValueError:
        return False


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
        self.saved_profile = None
        self.pairing = None
        self.runtime = {}
        self.setup_running = False
        self.preparing = False
        self.connecting = False
        self.status = {}
        self.polling = False
        self.starting = False
        self.closing = False
        self.cancel_prepare = threading.Event()
        self.close_started = None
        self.jobs = []
        self.network = {'state': 'unchecked', 'message': 'Check the connection to get started.'}
        self.network_checking = False
        self.network_setting_up = False
        self.network_checked_at = 0
        self.cancel_network = threading.Event()
        self.lan_interface = None
        self.setMinimumSize(620, 480)
        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(); scroll.setWidget(content); outer.addWidget(scroll)
        layout = QVBoxLayout(content)
        title = QLabel('DistributedExec')
        title.setStyleSheet('font-size: 25px; font-weight: 600')
        layout.addWidget(title)
        intro = QLabel('Host a workspace, contribute compute, or do both. Your browser is the editing dashboard.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.network_box = QGroupBox('Connect across networks')
        network_layout = QVBoxLayout(self.network_box)
        network_hint = QLabel('1. Install Tailscale on both computers. 2. Use the same account on your own computers, '
            'or invite another person through Tailscale to join your private network. '
            '3. Choose Different networks on the host and share its app invitation to pair the worker. You control Tailscale setup and sign-in.')
        network_hint.setWordWrap(True); network_layout.addWidget(network_hint)
        self.network_label = QLabel(self.network['message']); self.network_label.setWordWrap(True)
        network_layout.addWidget(self.network_label)
        self.network_devices = QLabel(''); self.network_devices.setWordWrap(True)
        self.network_devices.setTextInteractionFlags(Qt.TextSelectableByMouse)
        network_layout.addWidget(self.network_devices)
        peer_row = QHBoxLayout()
        self.network_peer_choice = QComboBox()
        peer_row.addWidget(self.network_peer_choice, 1)
        self.network_use_peer_button = self.button(peer_row, 'Use host address', self.use_peer_address)
        network_layout.addLayout(peer_row)
        network_buttons = QHBoxLayout()
        self.network_setup_button = self.button(network_buttons, 'Install Tailscale', self.setup_network)
        self.network_login_button = self.button(network_buttons, 'Open Tailscale sign-in', self.login_network)
        network_layout.addLayout(network_buttons)
        network_cancel = QHBoxLayout()
        self.network_check_button = self.button(network_cancel, 'Check connection', self.check_network)
        self.network_cancel_button = self.button(network_cancel, 'Cancel download', self.cancel_network_setup)
        network_layout.addLayout(network_cancel)
        layout.addWidget(self.network_box)
        self.network_box.hide()
        host_box = QGroupBox('Host a workspace · Docker is optional')
        host_form = QFormLayout(host_box)
        self.host_mode = QComboBox()
        self.host_mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.host_mode.setMinimumContentsLength(24)
        self.host_mode.addItem('Same network (Wi-Fi or Ethernet)', 'lan')
        self.host_mode.addItem('Different networks (Tailscale)', 'tailscale')
        self.host_mode.currentIndexChanged.connect(self.host_mode_changed)
        host_form.addRow('Connect over', self.host_mode)
        self.interface = QComboBox()
        self.interface.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.interface.setMinimumContentsLength(24)
        self.interface.setToolTip('Choose the network address that other computers on your private LAN can reach.')
        interfaces = psutil.net_if_stats()
        preferred = None
        for name, addresses in psutil.net_if_addrs().items():
            if name in interfaces and not interfaces[name].isup:
                continue
            for address in addresses:
                if address.family == socket.AF_INET and not ipaddress.ip_address(address.address).is_loopback:
                    self.interface.addItem(f'{name} · {address.address}', address.address)
                    if preferred is None and not ipaddress.ip_address(address.address).is_link_local:
                        preferred = address.address
        self.interface.addItem('This computer only · 127.0.0.1', '127.0.0.1')
        try:
            # UDP connect asks the OS which local address its route would use; it sends no payload.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect(('192.0.2.1', 9))
                routed = route.getsockname()[0]
            if self.interface.findData(routed) >= 0 and not ipaddress.ip_address(routed).is_link_local:
                preferred = routed
        except OSError:
            pass
        self.interface.setCurrentIndex(self.interface.findData(preferred or '127.0.0.1'))
        self.lan_interface = self.interface.currentData()
        host_form.addRow('Network interface', self.interface)
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(8000)
        host_form.addRow('Port', self.port)
        self.workspace_name = QLineEdit('default')
        self.workspace_name.setMaxLength(64)
        self.workspace_name.setToolTip('Use letters, numbers, underscores or hyphens. Each name keeps a separate workspace and job history.')
        host_form.addRow('Workspace name', self.workspace_name)
        self.host_label = QLabel('Not hosting'); self.host_label.setWordWrap(True)
        self.host_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_form.addRow(self.host_label)
        self.pair_label = QLabel('Pairing code appears here when hosting')
        self.pair_label.setWordWrap(True)
        self.pair_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_form.addRow(self.pair_label)
        invitation_buttons = QHBoxLayout()
        self.copy_invitation_button = self.button(invitation_buttons, 'Copy invitation for another computer', self.copy_invitation)
        self.copy_invitation_button.setToolTip('Copy one invitation containing the address and a pairing code. Share it privately; it expires with the code.')
        host_form.addRow(invitation_buttons)
        host_buttons = QHBoxLayout()
        self.host_start = self.button(host_buttons, 'Host workspace', self.start_host)
        self.dashboard_button = self.button(host_buttons, 'Open dashboard', self.open_dashboard)
        self.copy_address_button = self.button(host_buttons, 'Copy address', self.copy_address)
        host_form.addRow(host_buttons)
        pairing_buttons = QHBoxLayout()
        self.copy_code_button = self.button(pairing_buttons, 'Copy code', self.copy_code)
        self.rotate_button = self.button(pairing_buttons, 'New pairing code', self.rotate_code)
        self.stop_host_button = self.button(pairing_buttons, 'Stop host', self.stop_host)
        self.rotate_button.setToolTip('Create a fresh ten-minute code. Previously paired workers stay connected.')
        host_form.addRow(pairing_buttons)
        self.revoke_choice = QComboBox()
        revoke_row = QHBoxLayout(); revoke_row.addWidget(self.revoke_choice)
        self.revoke_button = self.button(revoke_row, 'Revoke worker', self.revoke_worker)
        self.revoke_button.setToolTip('Remove the selected computer\'s access. It must pair again to reconnect.')
        host_form.addRow('Paired computers', revoke_row)
        self.contribute = QCheckBox('Also contribute compute while hosting')
        self.contribute.setToolTip('Run a local worker alongside the host. Requires a ready Docker runtime and uses the worker resource budgets below.')
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
        self.check_button = self.button(runtime_buttons, 'Check again', self.check_docker)
        self.prepare_button = self.button(runtime_buttons, 'Prepare runtime', self.prepare)
        self.cancel_button = self.button(runtime_buttons, 'Cancel setup', self.cancel_setup)
        runtime_layout.addLayout(runtime_buttons)
        setup_buttons = QHBoxLayout()
        self.setup_button = self.button(setup_buttons, 'Set up compute', self.setup_compute)
        runtime_layout.addLayout(setup_buttons)
        help_buttons = QHBoxLayout()
        self.button(help_buttons, 'Windows setup help', lambda: webbrowser.open('https://docs.docker.com/desktop/setup/install/windows-install/'))
        self.button(help_buttons, 'Linux setup help', lambda: webbrowser.open('https://docs.docker.com/engine/install/'))
        runtime_layout.addLayout(help_buttons)
        setup_hint = QLabel('Set up compute downloads Docker if missing, opens its setup, then prepares the runtime. Existing Docker is reused.')
        setup_hint.setWordWrap(True)
        runtime_layout.addWidget(setup_hint)
        layout.addWidget(runtime_box)

        worker_box = QGroupBox('Join as a worker')
        form = QFormLayout(worker_box)
        self.invitation = QLineEdit(); self.invitation.setPlaceholderText('Paste the host’s connection invitation (DE1…)')
        self.invitation.setMaxLength(4096); self.invitation.setEchoMode(QLineEdit.Password)
        self.invitation.textChanged.connect(self.apply_invitation)
        form.addRow('Invitation', self.invitation)
        self.invitation_label = QLabel('Paste one invitation, or enter the address and pairing code below.')
        self.invitation_label.setWordWrap(True); form.addRow(self.invitation_label)
        self.address = QLineEdit(); self.address.setPlaceholderText('http://192.168.1.10:8000')
        self.code = QLineEdit(); self.code.setPlaceholderText('Expiring pairing code'); self.code.setEchoMode(QLineEdit.Password)
        self.address.textEdited.connect(self.invitation.clear)
        self.code.textEdited.connect(self.invitation.clear)
        self.code.setMaxLength(64)
        self.name = QLineEdit(socket.gethostname())
        self.name.setMaxLength(80)
        form.addRow('Host address', self.address); form.addRow('Pairing code', self.code); form.addRow('Computer name', self.name)
        show_code = QCheckBox('Show pairing code')
        show_code.toggled.connect(lambda shown: self.code.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password))
        form.addRow('', show_code)
        join_hint = QLabel('Both computers need the same private network. For different Wi-Fi networks, set up Tailscale on both computers first.')
        join_hint.setWordWrap(True); form.addRow(join_hint)
        self.network_help_button = QPushButton('Different Wi-Fi? Set up connection')
        self.network_help_button.clicked.connect(self.show_network_setup)
        form.addRow(self.network_help_button)
        budgets = QHBoxLayout()
        self.cpu = QDoubleSpinBox(); self.cpu.setRange(.1, max(1, os.cpu_count() or 1)); self.cpu.setValue(min(2, os.cpu_count() or 1)); self.cpu.setSingleStep(.5)
        self.memory = QSpinBox(); self.memory.setRange(64, min(131072, psutil.virtual_memory().total // 1048576)); self.memory.setValue(1024)
        self.concurrency = QSpinBox(); self.concurrency.setRange(1, 32); self.concurrency.setValue(1)
        self.cpu.setToolTip('Total CPU cores this worker may reserve across its running jobs.')
        self.memory.setToolTip('Total memory this worker may reserve, in MiB (1024 MiB = 1 GiB).')
        self.concurrency.setToolTip('Maximum chunks running at once, within the CPU and memory budgets.')
        for label, widget in [('CPU', self.cpu), ('RAM MiB', self.memory), ('Concurrency', self.concurrency)]:
            budgets.addWidget(QLabel(label)); budgets.addWidget(widget)
        form.addRow(QLabel('Resource budgets for this worker'))
        form.addRow(budgets)
        worker_buttons = QHBoxLayout()
        self.connect_button = self.button(worker_buttons, 'Connect', self.join)
        self.reconnect_button = self.button(worker_buttons, 'Reconnect saved', self.reconnect)
        self.reconnect_button.setToolTip('Reconnect the last paired worker using its saved credential and original resource budgets.')
        form.addRow(worker_buttons)
        worker_controls = QHBoxLayout()
        self.resume_button = self.button(worker_controls, 'Resume', lambda: self.worker_command('accepting'))
        self.pause_button = self.button(worker_controls, 'Pause new work', lambda: self.worker_command('paused'))
        form.addRow(worker_controls)
        disconnect_controls = QHBoxLayout()
        self.drain_button = self.button(disconnect_controls, 'Disconnect after work', lambda: self.worker_command('draining'))
        self.stop_worker_button = self.button(disconnect_controls, 'Stop now', lambda: self.worker_command('stop'))
        self.pause_button.setToolTip('Finish current jobs and stop accepting new ones. Select Resume to contribute again.')
        self.drain_button.setToolTip('Finish current jobs, then disconnect this worker.')
        self.stop_worker_button.setToolTip('Interrupt current work and disconnect this worker immediately.')
        form.addRow(disconnect_controls)
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
        self.address.textChanged.connect(self.address_changed)
        self.address_changed()
        self.update_controls()
        self.check_docker()

    def button(self, row, label, callback):
        button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button); return button

    def message(self, value):
        self.messages.appendPlainText(redacted(value))

    def update_controls(self):
        hosting = bool(self.host_process and self.host_process.poll() is None)
        working = bool(self.worker_process and self.worker_process.poll() is None)
        ready = self.runtime.get('state') == 'ready'
        busy = self.setup_running or self.preparing
        across = self.host_mode.currentData() == 'tailscale'
        self.host_start.setEnabled(not hosting and not self.starting and (not across or bool(network_ipv4(self.network))))
        self.host_start.setToolTip('Connect Tailscale first to host across networks.' if across and not network_ipv4(self.network) else 'Start this workspace coordinator.')
        for field in (self.host_mode, self.port, self.workspace_name):
            field.setEnabled(not hosting and not self.starting)
        self.interface.setEnabled(not hosting and not self.starting and not across)
        for button in (self.dashboard_button, self.copy_address_button, self.rotate_button):
            button.setEnabled(hosting and not self.starting)
        self.stop_host_button.setEnabled(hosting)
        self.copy_code_button.setEnabled(hosting and bool(self.pairing and self.pairing['expires'] > time.time()))
        self.copy_invitation_button.setEnabled(hosting and bool(self.host_url and self.pairing and self.pairing['expires'] > time.time()))
        self.revoke_button.setEnabled(hosting and self.revoke_choice.count() > 0)
        self.check_button.setEnabled(not busy)
        self.prepare_button.setEnabled(not busy and bool(self.runtime.get('endpoint')))
        self.setup_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and not self.cancel_prepare.is_set())
        worker_network_ready = not tailscale_address(self.address.text()) or bool(network_ipv4(self.network))
        invitation_ready = True
        if self.invitation.text().strip():
            try:
                value = decode_invitation(self.invitation.text().strip())
            except ValueError as exc:
                invitation_ready = False; self.invitation_label.setText(str(exc))
            else:
                seconds = max(0, int(value['expires'] - time.time()))
                self.invitation_label.setText(f'Invitation ready · expires in {seconds}s. Select Connect when compute is ready.')
        self.connect_button.setEnabled(ready and worker_network_ready and invitation_ready and not working and not self.connecting)
        self.connect_button.setToolTip('Set up compute before connecting a worker.' if not ready else
            'Connect Tailscale before joining this host.' if not worker_network_ready else
            'Paste a current invitation, or clear Invitation and enter the address and code.' if not invitation_ready else
            'Connect with a current invitation or pairing code.')
        self.reconnect_button.setEnabled(ready and not working and not self.connecting and bool(self.saved_profile or self.profile))
        for button in (self.resume_button, self.pause_button, self.drain_button, self.stop_worker_button):
            button.setEnabled(working)
        network_busy = self.network_checking or self.network_setting_up
        self.network_check_button.setEnabled(not network_busy)
        self.network_setup_button.setEnabled(not network_busy and self.network.get('state') in ('unchecked', 'missing'))
        self.network_login_button.setEnabled(not network_busy and self.network.get('state') in ('stopped', 'login_required'))
        self.network_cancel_button.setVisible(self.network_setting_up)
        self.network_cancel_button.setEnabled(self.network_setting_up and not self.cancel_network.is_set())
        self.network_use_peer_button.setEnabled(bool(self.network_peer_choice.currentData()) and not working and not self.connecting)

    def host_mode_changed(self):
        if self.host_mode.currentData() == 'tailscale':
            self.lan_interface = self.interface.currentData()
            self.show_network_setup()
            self.set_network(self.network)
        else:
            if self.interface.findData(self.lan_interface) >= 0:
                self.interface.setCurrentIndex(self.interface.findData(self.lan_interface))
            self.network_box.hide()
        self.update_controls()

    def show_network_setup(self):
        self.network_box.show()
        self.check_network()
        self.findChild(QScrollArea).ensureWidgetVisible(self.network_box)

    def check_network(self, checked=False):
        if self.network_checking or self.network_setting_up or self.closing:
            return
        self.network_checking = True; self.network_checked_at = time.monotonic()
        self.update_controls()
        def finished(value):
            self.network_checking = False; self.network_checked_at = time.monotonic()
            self.set_network(value)
        self.action(lambda _: check_network(), finished,
            lambda error: finished({'state': 'stopped', 'message': 'Cannot check Tailscale: ' + redacted(error)}))

    def set_network(self, value):
        self.network = value
        address = network_ipv4(value)
        self.network_label.setText(value.get('message', 'Check the Tailscale connection.'))
        selected_peer = self.network_peer_choice.currentData()
        self.network_peer_choice.clear()
        if address:
            label = f'This computer: {address}'
            if value.get('network'):
                label += ' · ' + value['network']
            devices = value.get('devices', [])
            for device in devices:
                peer_address = network_ipv4({'state': 'ready', 'ipv4': device.get('ipv4')})
                if peer_address:
                    self.network_peer_choice.addItem(f'{device["name"]} · {peer_address}', peer_address)
            if self.network_peer_choice.findData(selected_peer) >= 0:
                self.network_peer_choice.setCurrentIndex(self.network_peer_choice.findData(selected_peer))
            label += '\nOnline computers: ' + (', '.join(f'{device["name"]} ({device["ipv4"]})' for device in devices[:8])
                if devices else 'No other computers yet. Sign in on the other computer, then check again.')
            self.network_devices.setText(label)
            if self.host_mode.currentData() == 'tailscale' and not self.host_process and not self.starting:
                if self.interface.findData(address) < 0:
                    self.interface.addItem(f'Tailscale · {address}', address)
                self.interface.setCurrentIndex(self.interface.findData(address))
        else:
            self.network_devices.clear()
        self.update_controls()

    def use_peer_address(self):
        address = self.network_peer_choice.currentData()
        if not address:
            return
        self.invitation.clear()
        self.address.setText(f'http://{address}:8000')
        self.code.setFocus()
        self.findChild(QScrollArea).ensureWidgetVisible(self.address)
        self.message('Host address filled with port 8000. Enter the host’s pairing code, or paste its invitation for the exact address and code.')

    def address_changed(self):
        if tailscale_address(self.address.text()) and self.network_box.isHidden():
            self.show_network_setup()
        self.update_controls()

    def setup_network(self):
        if self.network_setting_up or self.network_checking:
            return
        self.network_setting_up = True; self.cancel_network.clear(); self.update_controls()
        def finished(value):
            self.network_setting_up = False; self.set_network(value)
            self.message(value.get('message', 'Finish Tailscale setup, then check the connection.'))
        def failed(error):
            self.network_setting_up = False; self.update_controls()
            self.network_label.setText(redacted(error)); self.message(error)
        def progress(value):
            self.network_label.setText(value.strip()); self.message(value)
        self.action(lambda report: setup_network(report, self.cancel_network), finished, failed, progress)

    def cancel_network_setup(self):
        self.cancel_network.set(); self.update_controls()
        self.message('Cancelling Tailscale download. An open installer remains under your control.')

    def login_network(self):
        if self.network_checking or self.network_setting_up:
            return
        self.network_checking = True; self.update_controls()
        def finished(value):
            self.network_checking = False; self.network_checked_at = time.monotonic()
            self.set_network(value)
            self.message(value.get('message', 'Finish Tailscale sign-in, then check the connection.'))
        self.action(lambda _: login_network(), finished,
            lambda error: finished({'state': 'stopped', 'message': redacted(error)}))

    def copy_invitation(self):
        if not self.host_url or not self.pairing or self.pairing['expires'] <= time.time():
            return
        try:
            invitation = encode_invitation(self.host_url, self.pairing['code'], self.pairing['expires'])
        except ValueError as exc:
            self.message(exc); return
        QApplication.clipboard().setText(invitation)
        self.message('Connection invitation copied. Share it privately and paste it into Invitation on the worker before it expires.')

    def apply_invitation(self, value=None):
        value = self.invitation.text().strip() if value is None else value.strip()
        if not value:
            self.invitation_label.setText('Paste one invitation, or enter the address and pairing code below.')
            return True
        try:
            decoded = decode_invitation(value)
        except ValueError as exc:
            self.invitation_label.setText(str(exc)); return False
        self.address.setText(decoded['address']); self.code.setText(decoded['code'])
        seconds = max(0, int(decoded['expires'] - time.time()))
        self.invitation_label.setText(f'Invitation ready · expires in {seconds}s. Select Connect when compute is ready.')
        return True

    def copy_address(self):
        if self.host_url:
            QApplication.clipboard().setText(self.host_url)
            self.message('Host address copied. Paste it into Join as a worker on the other computer.')

    def copy_code(self):
        if self.pairing and self.pairing['expires'] > time.time():
            QApplication.clipboard().setText(self.pairing['code'])
            self.message('Pairing code copied. Paste it into Join as a worker before the code expires.')

    def cancel_setup(self):
        self.cancel_prepare.set()
        self.message('Cancelling compute setup. An open Docker installer remains under your control.')
        self.update_controls()

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
            self.saved_profile = value.get('worker_profile')
            self.host_mode.setCurrentIndex(max(0, self.host_mode.findData(value.get('host_mode', 'lan'))))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_preferences(self):
        write_json(self.preferences(), {'host_address': self.address.text(), 'name': self.name.text(), 'cpu': self.cpu.value(),
            'memory_mb': self.memory.value(), 'concurrency': self.concurrency.value(), 'port': self.port.value(), 'workspace': self.workspace_name.text(),
            'host_mode': self.host_mode.currentData(),
            'worker_profile': str(self.profile or self.saved_profile) if self.profile or self.saved_profile else None})

    def reconnect(self):
        if self.worker_process or self.connecting or self.runtime.get('state') != 'ready':
            self.message('Prepare the runtime and disconnect any current worker before reconnecting.'); return
        try:
            preferences = json.loads(self.preferences().read_text(encoding='utf-8'))
            profile = Path(preferences['worker_profile']).resolve()
            if not profile.is_relative_to((data_dir() / 'workers').resolve()): raise ValueError('Invalid saved worker path')
            config = json.loads(profile.read_text(encoding='utf-8'))
            if config['endpoint'] != self.runtime['endpoint']: raise ValueError('Selected Docker endpoint changed; select the original context or pair a new worker')
            if tailscale_address(config.get('host', '')) and not network_ipv4(self.network):
                self.show_network_setup()
                self.message('Connect Tailscale before reconnecting this saved worker. Its saved pairing remains available.')
                return
            if self.host_process and not self.contribute.isChecked(): raise ValueError('Enable Also contribute compute while hosting first')
            self.profile = profile
            self.worker_process, self.worker_log = self.spawn('worker', ['--config', profile])
            self.worker_label.setText('Reconnecting to saved host…')
            self.update_controls()
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
        if self.starting or (self.host_process and self.host_process.poll() is None): return
        name = self.workspace_name.text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            self.message('Workspace name must contain 1–64 letters, numbers, underscores or hyphens.')
            self.workspace_name.setFocus(); return
        self.workspace = data_dir() / 'workspaces' / name
        across = self.host_mode.currentData() == 'tailscale'
        ip = network_ipv4(self.network) if across else self.interface.currentData()
        if across and not ip:
            self.show_network_setup()
            self.message('Connect Tailscale on this computer before hosting across networks. ' + self.network.get('message', ''))
            return
        port = self.port.value()
        self.host_url = f'http://{ip}:{port}'
        self.local_url = f'http://127.0.0.1:{port}'
        self.starting = True; self.host_label.setText('Starting coordinator…'); self.update_controls()
        self.save_preferences()
        def start(_):
            connection = check_network() if across else None
            host_ip = network_ipv4(connection) if across else ip
            if not host_ip:
                return connection, None, None, None, None
            token = admin_secret(self.workspace)
            process, output = self.spawn('host', ['--workspace', self.workspace, '--bind', host_ip, '--address', host_ip, '--port', port])
            return connection, host_ip, token, process, output
        def started(value):
            connection, host_ip, token, process, output = value
            if connection is not None:
                self.set_network(connection)
            if not host_ip:
                self.host_start_failed('Tailscale is not connected. ' + connection.get('message', 'Check the connection and try again.'))
                return
            self.host_url = f'http://{host_ip}:{port}'
            self.token, self.host_process, self.host_log = token, process, output
            self.host_label.setText('Starting coordinator…')
        self.action(start, started, self.host_start_failed)

    def host_start_failed(self, error):
        self.starting = False; self.host_label.setText('Could not start host: ' + redacted(error))
        self.update_controls(); self.message(error)

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
        self.update_controls()

    def open_dashboard(self):
        if not self.token or not self.host_process or self.host_process.poll() is not None:
            self.message('Host a workspace first.'); return
        self.action(lambda _: self.host_api('/api/desktop/ticket'), lambda value: webbrowser.open(self.local_url + '/#ticket=' + value['ticket']))

    def join(self, checked=False, local=False):
        if self.connecting:
            return
        if self.worker_process and self.worker_process.poll() is None:
            self.message('A worker is already connected. Use Pause, Disconnect or Stop now.'); return
        if self.runtime.get('state') != 'ready':
            self.message('Prepare the runtime before contributing compute. Hosting remains available.'); return
        if not local and self.host_process and self.host_process.poll() is None and not self.contribute.isChecked():
            self.message('Enable Also contribute compute while hosting to run both roles.'); return
        if local and (not self.pairing or self.pairing['expires'] <= time.time()):
            self.message('Create a new pairing code before contributing compute to this host.'); return
        if not local and self.invitation.text().strip() and not self.apply_invitation():
            self.message(self.invitation_label.text()); self.invitation.setFocus(); return
        if not local and tailscale_address(self.address.text()) and not network_ipv4(self.network):
            self.show_network_setup()
            self.message('Connect Tailscale on this computer before joining this host. ' + self.network.get('message', ''))
            return
        host = self.local_url if local else self.address.text().strip().rstrip('/')
        if host and '://' not in host:
            host = 'http://' + host
        try:
            parsed = urlsplit(host)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname and not any(char.isspace() for char in host)
                and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
                and (parsed.port is None or 1 <= parsed.port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            self.message('Use a host address such as http://192.168.1.10:8000 without a path or credentials.')
            self.address.setFocus(); return
        payload = {'code': self.pairing['code'] if local else self.code.text().strip(), 'name': self.name.text().strip(),
            'cpu': self.cpu.value(), 'memory_mb': self.memory.value(), 'concurrency': self.concurrency.value()}
        if not payload['code']:
            self.message('Enter the pairing code shown on the host computer. Ask for a new code if it has expired.')
            self.code.setFocus(); return
        if not payload['name']:
            self.message('Enter a computer name so the host can identify this worker.')
            self.name.setFocus(); return
        if payload['cpu'] > self.runtime.get('daemon_cpu', 128) or payload['memory_mb'] > self.runtime.get('daemon_memory_mb', 131072):
            self.message('Worker budget exceeds resources allocated to the Docker daemon. Adjust the budget or Docker Desktop resources.'); return
        if not local:
            self.address.setText(host)
        self.connecting = True; self.worker_label.setText('Connecting to host…')
        self.update_controls(); self.save_preferences()
        endpoint = self.runtime['endpoint']
        def connect(_):
            try:
                response = requests.post(host + '/api/pair', json=payload, timeout=(3, 5)); response.raise_for_status()
            except requests.Timeout as exc:
                raise RuntimeError('The host did not respond in time. Check that it is still hosting, then retry.') from exc
            except requests.ConnectionError as exc:
                raise RuntimeError('Cannot reach the host. Check its address, that it is hosting, and your private network or firewall settings.') from exc
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    if 'rate limit' in exc.response.text.lower():
                        raise RuntimeError('Too many pairing attempts. Wait one minute before trying again with the current host code.') from exc
                    raise RuntimeError('The pairing code is invalid or expired. Copy a new code from the host and try again.') from exc
                raise
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
            self.saved_profile = str(self.profile); self.connecting = False
            self.code.clear(); self.invitation.clear(); self.worker_label.setText('Paired; connecting worker…')
            self.update_controls(); self.save_preferences(); self.message(f'Paired. Credential stored in {location}.')
        def failed(error):
            self.connecting = False; self.worker_label.setText('Could not connect: ' + redacted(error))
            self.update_controls(); self.message(error)
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
        self.update_controls()
        if self.setup_running or self.preparing:
            return
        self.runtime_label.setText(f'{value["state"].capitalize()}: {value["message"]}')
        self.endpoint_label.setText(f'Selected context: {value.get("context", "unavailable")} · endpoint: {value.get("endpoint", "unavailable")}')

    def prepare(self):
        if self.setup_running or self.preparing:
            return
        if not self.runtime.get('endpoint'):
            self.message('Start Docker Desktop in Linux-container mode, then Check again.'); return
        self.preparing = True; self.cancel_prepare.clear(); self.update_controls()
        self.runtime_label.setText('Preparing runtime…')
        endpoint = self.runtime['endpoint']
        def finished(_):
            self.preparing = False; self.update_controls(); self.check_docker()
        def failed(error):
            self.preparing = False; self.update_controls(); self.runtime_label.setText('Failed: ' + error); self.message(error)
        self.action(lambda progress: prepare_runtime(endpoint, progress, self.cancel_prepare), finished, failed)

    def setup_compute(self):
        if self.setup_running or self.preparing:
            return
        from .setup import setup_compute
        self.setup_running = True
        self.cancel_prepare.clear()
        self.update_controls()
        def restore():
            self.setup_running = False
            self.update_controls()
        def finished(value):
            restore(); self.set_runtime(value); self.message('Compute setup complete.')
        def failed(error):
            restore(); self.runtime_label.setText(error); self.message(error)
        def progress(value):
            self.runtime_label.setText(value.strip()); self.message(value)
        self.action(lambda report: setup_compute(report, self.cancel_prepare), finished, failed, progress)

    def tick(self):
        if not self.network_box.isHidden() and not self.closing and time.monotonic() - self.network_checked_at >= 15:
            self.check_network()
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
            self.pair_label.setText(f'Pairing code: {self.pairing["code"]} · {seconds}s remaining' if seconds else 'Pairing code expired. Select New pairing code.')
        if self.profile and self.worker_process:
            try:
                state = json.loads((self.profile.parent / 'state.json').read_text(encoding='utf-8'))
                self.worker_label.setText(f'{state["connection"]} · {state["mode"]} · {len(state["active"])} active attempts\nReserved {state["reserved_cpu"]} CPU / {state["reserved_memory_mb"]} MiB RAM (not measured usage)' + ('\n' + redacted(state['error']) if state.get('error') else ''))
            except (OSError, ValueError): pass
        if self.host_process and self.token and not self.polling:
            self.polling = True
            def received(state):
                self.polling = False
                if not self.host_process:
                    return
                self.status = state
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
        self.update_controls()

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
            'host_mode': self.host_mode.currentData(), 'network_state': self.network.get('state'),
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
        self.closing = True; self.cancel_prepare.set(); self.cancel_network.set(); self.worker_command('stop')
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
