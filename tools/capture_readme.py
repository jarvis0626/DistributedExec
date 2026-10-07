"""Capture real README screenshots with isolated demo data and a prepared Docker runtime.

Run with .venv/Scripts/python.exe tools/capture_readme.py on Windows with Edge installed.
No installs, image preparation or remote-network sign-in are performed by this script.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from playwright.sync_api import sync_playwright
from distributedexec import launcher
from distributedexec.paths import config_dir
from distributedexec.runtime import DockerExecutor, check_runtime
from distributedexec.store import digest


def until(app, predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.05)
    raise RuntimeError('Screenshot demo did not reach the expected state')


def main():
    if check_runtime()['state'] != 'ready':
        raise RuntimeError('Start Docker and prepare the runtime before capturing screenshots')
    edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    browser_path = os.environ.get('DISTRIBUTEDEXEC_BROWSER') or str(edge)
    output = Path(__file__).resolve().parents[1] / 'docs/screenshots'
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and os.name == 'nt':
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        app.setFont(QFont('Segoe UI', 9))
    original_paths = (launcher.config_dir, launcher.data_dir, launcher.logs_dir)
    original_open = launcher.webbrowser.open
    credential_names = []
    with tempfile.TemporaryDirectory(prefix='DistributedExec-readme-') as directory:
        root = Path(directory)
        for name in ('config', 'data', 'logs'):
            (root / name).mkdir()
        launcher.config_dir = lambda: root / 'config'
        launcher.data_dir = lambda: root / 'data'
        launcher.logs_dir = lambda: root / 'logs'
        launcher.webbrowser.open = lambda _: True
        window = launcher.Launcher()
        worker_config = None
        try:
            window.resize(820, 1250)
            window.interface.setCurrentIndex(window.interface.findData('127.0.0.1'))
            window.workspace_name.setText('demo-workspace')
            window.name.setText('Demo worker')
            window.cpu.setValue(2); window.memory.setValue(1024); window.concurrency.setValue(2)
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', 8000))
                except OSError:
                    probe.bind(('127.0.0.1', 0))
                window.port.setValue(probe.getsockname()[1])
            window.show()
            until(app, lambda: not window.jobs and window.runtime.get('state') == 'ready')
            window.contribute.setChecked(True)
            window.start_host()
            credential_names.append('admin:' + str(window.workspace.resolve()))
            until(app, lambda: window.worker_process is not None and
                  any(w['mode'] == 'accepting' for w in window.status.get('workers', [])))
            worker_config = json.loads(window.profile.read_text(encoding='utf-8'))
            credential_names.append(worker_config['credential_ref'])
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                try:
                    context = browser.new_context(viewport={'width': 1440, 'height': 1140}, device_scale_factor=1)
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    # External services are neither needed nor contacted for the screenshots.
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(window.local_url) else route.abort())
                    ticket = window.host_api('/api/desktop/ticket')['ticket']
                    page.goto(window.local_url + '/#ticket=' + ticket)
                    page.wait_for_selector('.monaco-editor')
                    page.wait_for_function("document.getElementById('workerCount').textContent.includes('1')")
                    page.locator('#advancedSettings summary').click()
                    page.locator('#chunkSize').fill('2')
                    page.locator('#advancedSettings summary').click()
                    page.screenshot(path=str(output / 'dashboard.png'), full_page=True, animations='disabled')
                    page.locator('#runBtn').click()
                    page.wait_for_function("document.getElementById('jobTitle').textContent.includes('succeeded')", timeout=90000)
                    page.wait_for_selector('#download', state='visible')
                    job = window.host_api('/api/status', method='GET')['jobs'][0]['id']
                    result = window.host_api('/api/jobs/' + job + '/result', method='GET')
                    if result != [{'input': number, 'square': number * number} for number in range(1, 9)]:
                        raise RuntimeError('Screenshot job produced an unexpected result')
                    if errors:
                        raise RuntimeError('Dashboard raised a JavaScript error')
                    page.locator('#detail .job-diagnostics').first.locator('summary').click()
                    page.locator('#detail').screenshot(path=str(output / 'job-result.png'), animations='disabled')
                finally:
                    browser.close()
            until(app, lambda: not window.jobs)
            window.timer.stop()
            window.address.setText(window.local_url)
            window.pair_label.setText('Pairing code: hidden for screenshot · codes expire after 10 minutes')
            window.messages.clear()
            app.processEvents()
            if not window.grab().save(str(output / 'launcher.png')):
                raise RuntimeError('Could not save the launcher screenshot')
            print('Captured launcher, dashboard and a real four-chunk result in docs/screenshots')
        finally:
            window.timer.stop()
            window.closing = True
            window.worker_command('stop')
            if window.worker_process:
                try:
                    window.worker_process.wait(timeout=20)
                except Exception:
                    window.worker_process.terminate(); window.worker_process.wait(timeout=10)
            if window.host_process:
                try:
                    window.host_api('/api/desktop/shutdown')
                    window.host_process.wait(timeout=15)
                except Exception:
                    window.host_process.terminate(); window.host_process.wait(timeout=10)
            until(app, lambda: not window.jobs)
            for mode in ('host', 'worker'):
                handle = getattr(window, mode + '_log')
                if handle:
                    handle.close()
                setattr(window, mode + '_process', None)
            if worker_config:
                DockerExecutor(worker_config['endpoint'], worker_config['worker_id']).reconcile()
            window.close()
            launcher.config_dir, launcher.data_dir, launcher.logs_dir = original_paths
            launcher.webbrowser.open = original_open
            # Remove only the credentials created for this temporary demo.
            for name in credential_names:
                try:
                    import keyring
                    keyring.delete_password('DistributedExec', name)
                except Exception:
                    pass
                (config_dir() / (digest(name) + '.secret')).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
