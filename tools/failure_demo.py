"""Real two-worker, mid-chunk kill, lease expiry and ordered recovery demonstration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from distributedexec.credentials import admin_secret, save
from distributedexec.agent import write_json
from distributedexec.runtime import selected_endpoint, check_runtime, DockerExecutor


def wait_for(action, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = action()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError('Demo timed out')


def main():
    import socket
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    endpoint = selected_endpoint()[1]
    if check_runtime(endpoint)['state'] != 'ready':
        raise RuntimeError('Prepare runtime before running this demo')
    directory = args.output or Path(tempfile.mkdtemp(prefix='DistributedExec-demo-'))
    directory.mkdir(parents=True, exist_ok=True)
    workspace = directory / 'workspace'
    with socket.socket() as port_probe:
        port_probe.bind(('127.0.0.1', 0))
        port = port_probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    token = admin_secret(workspace)
    headers = {'Authorization': 'Bearer ' + token}
    processes, workers = [], []
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def post(route, value=None):
        result = requests.post(url + route, json=value, headers=headers, timeout=5)
        result.raise_for_status()
        return result.json()
    def get(route):
        result = requests.get(url + route, headers=headers, timeout=5)
        result.raise_for_status()
        return result.json()
    try:
        host = subprocess.Popen([sys.executable, str(ROOT / 'host.py'), '--workspace', str(workspace), '--bind', '127.0.0.1', '--port', str(port)], creationflags=flags)
        processes.append(host)
        def available():
            try:
                return get('/health')
            except requests.RequestException:
                return None
        wait_for(available, 20)
        pairing = post('/api/desktop/pairing')['code']
        def start_worker(index):
            response = requests.post(url + '/api/pair', json={'code': pairing, 'name': f'Demo worker {index}', 'cpu': 1, 'memory_mb': 256, 'concurrency': 1}, timeout=5)
            response.raise_for_status()
            pair = response.json()
            reference = 'demo:' + str(uuid.uuid4())
            save(reference, pair['credential'])
            service = directory / f'worker-{index}'
            service.mkdir()
            config = {'host': url, 'worker_id': pair['worker_id'], 'credential_ref': reference, 'endpoint': endpoint,
                      'service_dir': str(service), 'concurrency': 1}
            profile = service / 'profile.json'
            write_json(profile, config)
            process = subprocess.Popen([sys.executable, str(ROOT / 'worker.py'), '--config', str(profile)], creationflags=flags)
            processes.append(process)
            workers.append((pair['worker_id'], service))
            return process
        first = start_worker(1)
        wait_for(lambda: get('/api/status')['workers'][0]['mode'] == 'accepting', 20)
        code = (ROOT / 'examples/long_running.py').read_text(encoding='utf-8')
        job = post('/api/run', {'code': code, 'dataset': [1, 2, 3, 4], 'chunk_size': 2, 'timeout': 45})['job_id']
        wait_for(lambda: get('/api/jobs/' + job)['attempts'], 20)
        # Prove logs arrive before completion and the heartbeat runs independently.
        wait_for(lambda: get(f'/api/jobs/{job}/logs'), 15)
        first.kill()
        first.wait(timeout=5)
        start_worker(2)
        print('Killed worker 1 mid-chunk. Waiting for server-side lease expiry and bounded reassignment...', flush=True)
        detail = wait_for(lambda: (info if (info := get('/api/jobs/' + job))['status'] in ('succeeded', 'failed') else None), 100)
        result = get(f'/api/jobs/{job}/result')
        assert detail['status'] == 'succeeded', detail
        assert result == [2, 4, 6, 8], result
        assert any(attempt['state'] == 'expired' for attempt in detail['attempts'])
        assert len([attempt for attempt in detail['attempts'] if attempt['state'] == 'succeeded']) == 2
        (directory / 'report.json').write_text(json.dumps({'job': detail, 'result': result, 'two_local_workers_add_capacity': False}, indent=2), encoding='utf-8')
        print(f'PASS: ordered output {result}, two accepted chunks, preserved expired attempt. Report: {directory / "report.json"}', flush=True)
    finally:
        for _, service in workers:
            write_json(service / 'command.json', {'command': 'stop'})
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        for worker, _ in workers:
            DockerExecutor(endpoint, worker).reconcile()


if __name__ == '__main__':
    main()
