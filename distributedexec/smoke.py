"""Acceptance path runnable inside the packaged executable, without Python installed."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import requests
from .paths import assets, logs_dir
from .credentials import admin_secret, save
from .agent import write_json
from .runtime import check_runtime
from .network import encode_invitation, decode_invitation


def smoke():
    from .launcher import command
    report = {'application': 'DistributedExec', 'assets': [], 'checks': [], 'docker': 'not tested'}
    for name in ['static/index.html', 'static/app.js', 'static/vendor/monaco/vs/loader.js',
                 'runtime/Dockerfile', 'runtime/runner.py', 'migrations/001_initial.sql', 'licenses/Monaco-LICENSE',
                 'docs/REMOTE_CONNECTIVITY.md']:
        assert (assets() / name).is_file(), name
        report['assets'].append(name)
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from . import launcher
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and os.name == 'nt':
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        app.setFont(QFont('Segoe UI', 9))
    with tempfile.TemporaryDirectory(prefix='DistributedExec-smoke-') as directory:
        root = Path(directory)
        (root / 'config').mkdir()
        launcher.config_dir = lambda: root / 'config'
        launcher.data_dir = lambda: root / 'data'
        window = launcher.Launcher(); window.show()
        deadline = time.monotonic() + 15
        while window.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.05)
        assert not window.jobs, 'Qt background setup check timed out'
        window.grab().save(str(logs_dir() / 'launcher-smoke.png'))
        window.close(); app.processEvents()
        report['checks'].append('Native Qt launcher rendered on Windows with offscreen platform')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        workspace = root / 'workspace'
        token = admin_secret(workspace)
        headers = {'Authorization': 'Bearer ' + token}
        url = f'http://127.0.0.1:{port}'
        processes = []
        output = open(logs_dir() / 'smoke-services.log', 'w', encoding='utf-8')
        def spawn(mode, *arguments):
            process = subprocess.Popen(command(mode, *arguments, '--owner-pid', os.getpid()), stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            processes.append(process)
            return process
        def call(path, payload=None, method='POST'):
            response = requests.request(method, url + path, headers=headers, json=payload, timeout=3)
            response.raise_for_status(); return response.json()
        def wait_for(predicate, timeout=40):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    result = predicate()
                    if result: return result
                except requests.RequestException:
                    pass
                time.sleep(.1)
            raise RuntimeError('Packaged service acceptance timed out')
        worker_config = None
        try:
            host = spawn('host', '--workspace', workspace, '--bind', '127.0.0.1', '--port', port)
            wait_for(lambda: call('/health', method='GET'))
            assert requests.get(url + '/api/status', timeout=3).status_code == 401
            job = call('/api/run', {'code': 'def task(data): return data', 'dataset': []})['job_id']
            assert call('/api/jobs/' + job + '/result', method='GET') == []
            report['checks'].append('Windowless packaged host service, authentication and durable empty result')
            ticket = call('/api/desktop/ticket')['ticket']
            session = requests.Session()
            response = session.post(url + '/api/session', json={'ticket': ticket}, timeout=3)
            response.raise_for_status()
            assert session.get(url + '/api/status', timeout=3).status_code == 200
            report['checks'].append('Local browser session bootstrap')
            pairing = call('/api/desktop/pairing')
            invitation = decode_invitation(encode_invitation(url, pairing['code'], pairing['expires']))
            assert invitation['address'] == url and invitation['code'] == pairing['code']
            report['checks'].append('Bundled expiring connection invitation encoded and decoded')
            runtime = check_runtime()
            if runtime['state'] == 'ready':
                response = requests.post(invitation['address'] + '/api/pair', json={'code': invitation['code'], 'name': 'Packaged smoke worker', 'cpu': 1, 'memory_mb': 256}, timeout=3)
                response.raise_for_status(); worker = response.json()
                reference = 'smoke:' + worker['worker_id']; save(reference, worker['credential'])
                service = root / 'worker'; service.mkdir()
                worker_config = {'host': url, 'worker_id': worker['worker_id'], 'credential_ref': reference,
                    'endpoint': runtime['endpoint'], 'service_dir': str(service), 'concurrency': 1}
                profile = service / 'profile.json'; write_json(profile, worker_config)
                agent = spawn('worker', '--config', profile)
                wait_for(lambda: any(w['mode'] == 'accepting' for w in call('/api/status', method='GET')['workers']))
                job = call('/api/run', {'code': 'def task(data):\n    print("packaged execution")\n    return [x*2 for x in data]', 'dataset': [1, 2, 3, 4], 'chunk_size': 2})['job_id']
                detail = wait_for(lambda: (value if (value := call('/api/jobs/' + job, method='GET'))['status'] in ('succeeded', 'failed') else None))
                assert detail['status'] == 'succeeded', detail
                assert call('/api/jobs/' + job + '/result', method='GET') == [2, 4, 6, 8]
                assert call('/api/jobs/' + job + '/logs', method='GET')
                write_json(service / 'command.json', {'command': 'draining'}); agent.wait(timeout=15)
                assert agent.returncode == 0
                report['docker'] = 'passed: packaged worker, two Docker chunks, live logs and ordered full download'
            else:
                report['docker'] = f'skipped: {runtime["state"]}'
            call('/api/desktop/shutdown'); host.wait(timeout=10)
            assert host.returncode == 0
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=10)
            output.close()
            if worker_config:
                from .runtime import DockerExecutor
                DockerExecutor(worker_config['endpoint'], worker_config['worker_id']).reconcile()
    (logs_dir() / 'smoke-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0
