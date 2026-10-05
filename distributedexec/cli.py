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
    if args.mode == 'worker':
        from .agent import Agent
        agent = Agent(json.loads(args.config.read_text(encoding='utf-8')))
        try:
            agent.run(owner_check(args.owner_pid))
        except KeyboardInterrupt:
            agent.stop_now()
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
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((args.bind, args.port))
        listener.listen(128)
        actual_port = listener.getsockname()[1]
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
        server.run(sockets=[listener])
    except OSError as exc:
        raise RuntimeError(f'Cannot host on {args.bind}:{args.port}; choose a free port. No other process was stopped.') from exc
    finally:
        if listener:
            listener.close()
        lock.release()
    return 0
