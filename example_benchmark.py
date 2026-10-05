"""Equivalent local versus full distributed timing; verify every result."""
import argparse
import os
from pathlib import Path
import time
import uuid
import requests
from distributedexec.credentials import admin_secret
from distributedexec.paths import data_dir
from examples.cpu_heavy import task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='http://127.0.0.1:8000')
    parser.add_argument('--workspace', type=Path, default=data_dir() / 'workspaces/default')
    parser.add_argument('--items', type=int, default=32)
    parser.add_argument('--chunk-size', type=int, default=16)
    parser.add_argument('--runs', type=int, default=2)
    args = parser.parse_args()
    if args.items < 0 or args.runs < 1:
        parser.error('Items must be nonnegative and runs positive')
    credential = os.environ.get('DISTRIBUTEDEXEC_ADMIN_TOKEN') or admin_secret(args.workspace)
    headers = {'Authorization': 'Bearer ' + credential}
    code = (Path(__file__).resolve().parent / 'examples/cpu_heavy.py').read_text(encoding='utf-8')
    dataset = list(range(args.items))
    started = time.perf_counter()
    expected = task(dataset)
    local = time.perf_counter() - started
    print(f'Local equivalent workload: {local:.3f}s; {args.items} items')
    print('Runtime must already be prepared. First-job and warm labels do not imply controlled OS/image caches.')
    for index in range(args.runs):
        started = time.perf_counter()
        response = requests.post(args.host.rstrip('/') + '/api/run', json={'code': code, 'dataset': dataset,
            'chunk_size': args.chunk_size}, headers={**headers, 'Idempotency-Key': str(uuid.uuid4())}, timeout=10)
        response.raise_for_status()
        job = response.json()['job_id']
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = requests.get(args.host.rstrip('/') + '/api/jobs/' + job, headers=headers, timeout=10)
            response.raise_for_status(); status = response.json()
            if status['status'] in ('failed', 'cancelled'):
                raise RuntimeError(status['error'])
            if status['status'] == 'succeeded':
                break
            time.sleep(.1)
        else:
            raise RuntimeError('No completion within 10 minutes; check eligible workers')
        response = requests.get(args.host.rstrip('/') + '/api/jobs/' + job + '/result', headers=headers, timeout=10)
        response.raise_for_status()
        assert response.json() == expected, 'Distributed output differs from equivalent local workload'
        elapsed = time.perf_counter() - started
        label = 'first-job (cold service, if newly started)' if index == 0 else 'warm service'
        print(f'{label}: {elapsed:.3f}s end-to-end; equality verified. Local / distributed ratio: {local / elapsed:.2f}')
    print('End-to-end timing includes submission, queueing, new-container startup, transfer, aggregation and result download. Small jobs may be slower.')


if __name__ == '__main__':
    main()
