import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser
import psutil
import requests
from filelock import FileLock, Timeout
from .paths import data_dir, logs_dir


def configure_logging(service):
    handler = RotatingFileHandler(logs_dir() / f'{service}.log', maxBytes=1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def owner_check(pid):
    if not pid:
        return lambda: True
    try:
        owner = psutil.Process(pid)
        created = owner.create_time()
    except psutil.Error:
        return lambda: False
    def alive():
        try:
            return owner.is_running() and owner.create_time() == created
        except psutil.Error:
            return False
    return alive


def main(argv=None):
    parser = argparse.ArgumentParser(prog='DistributedExec')
    sub = parser.add_subparsers(dest='mode')
    host = sub.add_parser('host')
    host.add_argument('--workspace', type=Path, default=data_dir() / 'workspaces/default')
    host.add_argument('--bind', default='0.0.0.0')
    host.add_argument('--port', type=int, default=8000)
    host.add_argument('--address', action='append', default=[])
    host.add_argument('--open-browser', action='store_true')
    host.add_argument('--owner-pid', type=int)
    worker = sub.add_parser('worker')
    worker.add_argument('--config', type=Path, required=True)
    worker.add_argument('--owner-pid', type=int)
    runtime = sub.add_parser('prepare-runtime')
    runtime.add_argument('--endpoint')
    pair = sub.add_parser('pair')
    pair.add_argument('--host', required=True)
    pair.add_argument('--code')
    pair.add_argument('--name', default=socket.gethostname())
    pair.add_argument('--cpu', type=float, default=2)
    pair.add_argument('--memory-mb', type=int, default=1024)
    pair.add_argument('--concurrency', type=int, default=1)
    sub.add_parser('check-runtime')
    sub.add_parser('gui')
    sub.add_parser('smoke')
    args = parser.parse_args(argv)
    if args.mode in (None, 'gui'):
        from .launcher import launch
        return launch()
    configure_logging(args.mode)
    if args.mode == 'smoke':
        from .smoke import smoke
        return smoke()
    if args.mode == 'check-runtime':
        from .runtime import check_runtime
        value = check_runtime()
        if sys.stdout:
            print(json.dumps(value))
        return 0 if value['state'] == 'ready' else 1
    if args.mode == 'prepare-runtime':
        from .runtime import prepare_runtime, selected_endpoint
        prepare_runtime(args.endpoint or selected_endpoint()[1], lambda p: print(p) if sys.stdout else None)
        return 0
    if args.mode == 'pair':
        import getpass
        from urllib.parse import urlsplit
        from .models import PairRequest
        from .credentials import save
        from .runtime import check_runtime
        from .agent import write_json
        address = args.host.rstrip('/')
        url = urlsplit(address)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise RuntimeError('Use a host URL without a path or credentials')
        runtime = check_runtime()
        if runtime['state'] != 'ready':
            raise RuntimeError(runtime['message'])
        request = PairRequest(code=args.code or getpass.getpass('Pairing code: '), name=args.name, cpu=args.cpu, memory_mb=args.memory_mb, concurrency=args.concurrency)
        response = requests.post(address + '/api/pair', json=request.model_dump(), timeout=(3, 5))
        response.raise_for_status()
        worker = response.json()
        reference = 'worker:' + worker['worker_id']
        save(reference, worker['credential'])
        directory = data_dir() / 'workers' / worker['worker_id']
        profile = directory / 'profile.json'
        write_json(profile, {'host': address, 'worker_id': worker['worker_id'], 'credential_ref': reference,
            'endpoint': runtime['endpoint'], 'service_dir': str(directory), 'concurrency': args.concurrency})
        if sys.stdout:
            print(f'Paired. Start with: python worker.py --config "{profile}"')
        return 0
    if args.mode == 'worker':
        from .agent import Agent
        agent = Agent(json.loads(args.config.read_text(encoding='utf-8')))
        worker_lock = FileLock(agent.root / 'agent.lock')
        try:
            worker_lock.acquire(timeout=0)
            agent.run(owner_check(args.owner_pid))
        except Timeout:
            raise RuntimeError('This worker profile is already running')
        except KeyboardInterrupt:
            agent.stop_now()
        finally:
            worker_lock.release()
        return 0
    from .api import create_app
    from .credentials import admin_secret
    import uvicorn
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    lock = FileLock(workspace / 'coordinator.lock')
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise RuntimeError('This workspace is already hosted by another coordinator')
    listener = None
    local_listener = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((args.bind, args.port))
        listener.listen(128)
        actual_port = listener.getsockname()[1]
        sockets = [listener]
        if args.bind not in ('0.0.0.0', '127.0.0.1'):
            local_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name == 'nt':
                local_listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            local_listener.bind(('127.0.0.1', actual_port))
            local_listener.listen(128)
            sockets.append(local_listener)
        token = admin_secret(workspace)
        hosts = ['127.0.0.1', 'localhost', args.bind, *args.address]
        server = None
        app = create_app(workspace, token, hosts, shutdown=lambda: setattr(server, 'should_exit', True))
        config = uvicorn.Config(app, log_config=None, access_log=False, host=args.bind, port=actual_port)
        server = uvicorn.Server(config)
        alive = owner_check(args.owner_pid)
        def monitor():
            while not server.should_exit:
                if not alive():
                    server.should_exit = True
                    return
                time.sleep(1)
        threading.Thread(target=monitor, daemon=True).start()
        if args.open_browser:
            def browser():
                url = f'http://127.0.0.1:{actual_port}'
                for _ in range(50):
                    try:
                        response = requests.post(url + '/api/desktop/ticket', headers={'Authorization': 'Bearer ' + token}, timeout=1)
                        response.raise_for_status()
                        webbrowser.open(url + '/#ticket=' + response.json()['ticket'])
                        return
                    except requests.RequestException:
                        time.sleep(0.2)
            threading.Thread(target=browser, daemon=True).start()
        server.run(sockets=sockets)
    except OSError as exc:
        raise RuntimeError(f'Cannot host on {args.bind}:{args.port}; choose a free port. No other process was stopped.') from exc
    finally:
        if listener:
            listener.close()
        if local_listener:
            local_listener.close()
        lock.release()
    return 0
