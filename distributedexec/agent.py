"""Worker service. No submitted Python code executes in this process."""
import json
import logging
import threading
import time
import uuid
from pathlib import Path
import requests
from .credentials import load
from .models import Completion
from .paths import private_file
from .runtime import DockerExecutor, check_runtime
from .store import encode

LOG = logging.getLogger(__name__)


def write_json(path, value):
    staging = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        private_file(staging, encode(value))
        for attempt in range(6):
            try:
                staging.replace(path)
                return
            except OSError as exc:
                # Windows readers can briefly deny replacement; retain the last complete file.
                if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        staging.unlink(missing_ok=True)


class Agent:
    def __init__(self, config):
        self.config = config
        self.host = config['host'].rstrip('/')
        self.credential = load(config['credential_ref'])
        if not self.credential:
            raise RuntimeError('Stored worker credential unavailable; pair again')
        self.root = Path(config['service_dir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.outbox = self.root / 'outbox'
        self.outbox.mkdir(exist_ok=True)
        self.executor = DockerExecutor(config['endpoint'], config['worker_id'])
        self.active = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.mode = 'accepting'
        self.error = None
        self.connection = 'connecting'
        self.runtime_id = None
        self.hb_wake = threading.Event()
        self.hb_ready = threading.Event()
        self.sent_logs = {}

    def request(self, route, payload=None, token=None, retry=True):
        headers = {'Authorization': 'Bearer ' + self.credential}
        if token:
            headers['X-Attempt-Token'] = token
        last = None
        for index in range(3 if retry else 1):
            try:
                response = requests.post(self.host + route, json=payload, headers=headers, timeout=(3, 5))
                response.raise_for_status()
                return response.json()
            except requests.HTTPError:
                raise
            except requests.RequestException as exc:
                last = exc
                if index < 2:
                    self.stop.wait(0.5 * (index + 1))
        raise last

    def heartbeat(self):
        while not self.stop.is_set():
            with self.lock:
                ids = list(self.active)
            sent = time.monotonic()
            try:
                response = self.request('/api/worker/heartbeat', {'attempts': ids, 'mode': self.mode, 'runtime_id': self.runtime_id}, retry=False)
                with self.lock:
                    for attempt in ids:
                        active = self.active.get(attempt)
                        if not active:
                            continue
                        if attempt in response['renewed']:
                            # Use the send time, not the receive time: response latency never extends safety.
                            active['deadline'] = sent + response['renewed'][attempt] - 5
                        else:
                            active['stop'].set()
                self.connection = 'connected'
                self.error = None
                self.hb_ready.set()
            except Exception as exc:
                self.connection = 'disconnected'
                self.error = str(exc)[:1500]
                if isinstance(exc, requests.HTTPError) and exc.response.status_code == 401:
                    self.mode = 'stopped'
                    self.stop_now()
            finally:
                self.hb_wake.wait(3)
                self.hb_wake.clear()

    def stop_now(self):
        self.mode = 'stopped'
        with self.lock:
            for item in self.active.values():
                item['stop'].set()

    def commands(self):
        path = self.root / 'command.json'
        if not path.exists():
            return
        try:
            command = json.loads(path.read_text(encoding='utf-8'))['command']
            path.unlink(missing_ok=True)
            if command == 'stop':
                self.stop_now()
            elif command in ('paused', 'accepting', 'draining'):
                self.mode = command
            self.hb_wake.set()
        except (OSError, ValueError, KeyError):
            pass

    def report(self, path):
        pending = json.loads(path.read_text(encoding='utf-8'))
        task = pending['task']
        route = f"/api/worker/attempts/{task['attempt_id']}"
        try:
            for log in pending['logs'][self.sent_logs.get(task['attempt_id'], 0):]:
                try:
                    self.request(route + '/logs', log, task['attempt_token'])
                except requests.HTTPError as exc:
                    # A tighter coordinator log limit should truncate logs, not lose the completion.
                    if exc.response.status_code != 409 or 'log limit' not in exc.response.text.lower():
                        raise
                self.sent_logs[task['attempt_id']] = log['seq'] + 1
            if pending.get('completion'):
                self.request(route + '/complete', pending['completion'], task['attempt_token'])
                path.unlink(missing_ok=True)
        except requests.HTTPError as exc:
            if exc.response.status_code in (401, 409, 404):
                LOG.info('Discarding rejected stale report for attempt %s (HTTP %s)', task['attempt_id'], exc.response.status_code)
                path.unlink(missing_ok=True)
            else:
                raise
        if not path.exists():
            self.sent_logs.pop(task['attempt_id'], None)
            with self.lock:
                item = self.active.get(task['attempt_id'])
                if item:
                    item['reported'].set()

    def reporter(self):
        while not self.stop.is_set():
            for path in list(self.outbox.glob('*.json'))[:32]:
                if self.stop.is_set():
                    break
                try:
                    self.report(path)
                except Exception:
                    pass  # Durable outbox is replayed with the same sequence IDs and completion hash.
            self.stop.wait(1)

    def safety(self, owner_alive):
        while not self.stop.is_set():
            if not owner_alive():
                self.stop_now()
            with self.lock:
                for item in self.active.values():
                    if time.monotonic() >= item['deadline']:
                        item['stop'].set()
            self.stop.wait(0.1)

    def execute(self, task):
        attempt = task['attempt_id']
        spool = self.outbox / (attempt + '.json')
        pending = {'task': {'attempt_id': attempt, 'attempt_token': task['attempt_token']}, 'logs': [], 'completion': None}
        spool_lock = threading.Lock()
        write_json(spool, pending)

        def log(stream, content):
            with spool_lock:
                pending['logs'].append({'seq': len(pending['logs']), 'stream': stream, 'text': content})
                write_json(spool, pending)

        def persist(response):
            completion = Completion.model_validate(response).model_dump()
            with spool_lock:
                pending['completion'] = completion
                write_json(spool, pending)

        try:
            self.executor.execute(task, self.active[attempt]['stop'], log, persist=persist)
            while not self.active[attempt]['reported'].wait(0.2):
                if time.monotonic() >= self.active[attempt]['deadline']:
                    break
        except Exception as exc:
            self.error = str(exc)[:1500]
            LOG.exception('Attempt execution/report failed: %s', attempt)
        finally:
            with self.lock:
                self.active.pop(attempt, None)
            self.hb_wake.set()

    def run(self, owner_alive=lambda: True):
        runtime = check_runtime(self.config['endpoint'])
        if runtime['state'] != 'ready':
            raise RuntimeError(runtime['message'])
        self.runtime_id = runtime['image_id']
        self.executor.reconcile()
        heartbeat = threading.Thread(target=self.heartbeat, daemon=True)
        heartbeat.start()
        reporter = threading.Thread(target=self.reporter, daemon=True)
        reporter.start()
        safety = threading.Thread(target=self.safety, args=(owner_alive,), daemon=True)
        safety.start()
        try:
            while True:
                self.commands()
                if not owner_alive():
                    self.stop_now()
                with self.lock:
                    active = list(self.active.items())
                    for _, item in active:
                        if time.monotonic() >= item['deadline']:
                            item['stop'].set()
                    snapshots = [{key: item[key] for key in ('cpu', 'memory_mb', 'ordinal', 'job_id')} | {'id': ident} for ident, item in active]
                write_json(self.root / 'state.json', {'connection': self.connection, 'mode': self.mode, 'error': self.error,
                    'active': snapshots, 'reserved_cpu': sum(x['cpu'] for x in snapshots),
                    'reserved_memory_mb': sum(x['memory_mb'] for x in snapshots), 'updated': time.time()})
                if self.mode in ('draining', 'stopped') and not active:
                    break
                if self.mode == 'accepting' and self.hb_ready.is_set() and len(active) < self.config['concurrency']:
                    try:
                        sent = time.monotonic()
                        task = self.request('/api/worker/claim', retry=False)
                        if task.get('attempt_id'):
                            ident = task['attempt_id']
                            with self.lock:
                                self.active[ident] = {'stop': threading.Event(), 'deadline': sent + task['lease_seconds'] - 5,
                                    'reported': threading.Event(), 'cpu': task['cpu'], 'memory_mb': task['memory_mb'], 'ordinal': task['ordinal'], 'job_id': task['job_id']}
                            threading.Thread(target=self.execute, args=(task,), daemon=True).start()
                            self.hb_wake.set()
                    except Exception as exc:
                        self.error = str(exc)[:1500]
                self.stop.wait(0.3)
        finally:
            self.stop_now()
            until = time.monotonic() + 12
            while self.active and time.monotonic() < until:
                time.sleep(0.1)
            self.stop.set()
            self.hb_wake.set()
            heartbeat.join(timeout=9)
            reporter.join(timeout=9)
            safety.join(timeout=2)
            try:
                self.request('/api/worker/heartbeat', {'attempts': [], 'mode': 'stopped', 'runtime_id': self.runtime_id}, retry=False)
            except Exception:
                pass
            self.executor.reconcile()
