import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
import docker
from docker.types import LogConfig
from .paths import assets, data_dir, private_file
from .store import encode, digest, MAX_OUTPUT, MAX_LOG

IMAGE = 'distributedexec-runtime:stdlib-v1'


def remove_attempt(path, root):
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise RuntimeError('Refusing cleanup outside the owned attempt directory')
    def writable(function, filename, error):
        file = Path(filename)
        if file.is_symlink():
            file.unlink()
            return
        os.chmod(file, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(filename)
    shutil.rmtree(path, onexc=writable)


def selected_endpoint():
    """Read the selected Docker CLI context without executing its CLI or silently switching."""
    root = Path(os.environ.get('DOCKER_CONFIG', Path.home() / '.docker'))
    config = json.loads((root / 'config.json').read_text(encoding='utf-8')) if (root / 'config.json').exists() else {}
    context = os.environ.get('DOCKER_CONTEXT') or config.get('currentContext', 'default')
    if context == 'default':
        endpoint = os.environ.get('DOCKER_HOST') or ('npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///var/run/docker.sock')
    else:
        endpoint = None
        for path in (root / 'contexts' / 'meta').glob('*/meta.json'):
            info = json.loads(path.read_text(encoding='utf-8'))
            if info.get('Name') == context:
                endpoint = info['Endpoints']['docker']['Host']
                break
        if endpoint is None:
            raise RuntimeError(f'Docker context {context} has no readable endpoint')
    if not endpoint.startswith(('npipe://', 'unix://')):
        raise RuntimeError(f'Context {context} uses a remote daemon. V1 requires a local named pipe or Unix socket.')
    return context, endpoint


def client_for(endpoint=None):
    selected = endpoint or selected_endpoint()[1]
    if not selected.startswith(('npipe://', 'unix://')):
        raise RuntimeError('Only local Docker endpoints are supported')
    if os.name == 'nt':
        for directory in [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/resources/bin',
                          Path('C:/Program Files/Docker/Docker/resources/bin')]:
            if (directory / 'docker-credential-desktop.exe').is_file() and str(directory) not in os.environ.get('PATH', '').split(os.pathsep):
                os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
    return docker.DockerClient(base_url=selected, timeout=8)


def runtime_hash():
    return digest((assets() / 'runtime' / 'Dockerfile').read_text(encoding='utf-8') +
                  (assets() / 'runtime' / 'runner.py').read_text(encoding='utf-8'))


def approval_path(endpoint):
    return data_dir() / 'runtime' / (digest(endpoint) + '.json')


def approved_image(client, endpoint):
    path = approval_path(endpoint)
    if not path.exists():
        raise RuntimeError('Prepare the approved runtime first')
    approval = json.loads(path.read_text(encoding='utf-8'))
    if approval['context_hash'] != runtime_hash():
        raise RuntimeError('Runtime changed; prepare it again')
    image = client.images.get(approval['image_id'])
    return image.id


def check_runtime(endpoint=None):
    client = None
    try:
        context, selected = selected_endpoint()
        endpoint = endpoint or selected
        client = client_for(endpoint)
        info = client.info()
        if info['OSType'] != 'linux':
            return {'state': 'wrong mode', 'message': 'Switch Docker Desktop to Linux containers', 'endpoint': endpoint}
        try:
            image = approved_image(client, endpoint)
            return {'state': 'ready', 'message': 'Approved runtime ready', 'image_id': image, 'endpoint': endpoint, 'context': context,
                    'daemon_cpu': info['NCPU'], 'daemon_memory_mb': info['MemTotal'] // 1048576}
        except (RuntimeError, docker.errors.ImageNotFound):
            return {'state': 'preparing runtime', 'message': 'Docker is ready. Select Prepare runtime.', 'endpoint': endpoint, 'context': context}
    except Exception as exc:
        installed = (Path.home() / '.docker').exists() or shutil.which('docker') is not None
        return {'state': 'installed but stopped' if installed else 'missing', 'message': str(exc)[:1000]}
    finally:
        if client:
            client.close()


def prepare_runtime(endpoint, progress=lambda _: None, cancel=None):
    client = client_for(endpoint)
    try:
        if client.info()['OSType'] != 'linux':
            raise RuntimeError('Linux containers are required')
        stream = client.api.build(path=str(assets() / 'runtime'), tag=IMAGE, decode=True, rm=True)
        try:
            for item in stream:
                if cancel and cancel.is_set():
                    raise RuntimeError('Runtime preparation cancelled; existing approval is unchanged')
                if 'error' in item:
                    raise RuntimeError(item['error'])
                progress(item.get('stream') or item.get('status', 'Preparing runtime'))
        finally:
            stream.close()
        if cancel and cancel.is_set():
            raise RuntimeError('Runtime preparation cancelled')
        image = client.images.get(IMAGE)
        private_file(approval_path(endpoint), encode({'image_id': image.id, 'context_hash': runtime_hash()}))
        return image.id
    finally:
        client.close()


class DockerExecutor:
    def __init__(self, endpoint, worker_id, root=None):
        self.endpoint = endpoint
        self.worker_id = worker_id
        self.root = Path(root or data_dir() / 'attempts' / worker_id).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def reconcile(self):
        client = client_for(self.endpoint)
        try:
            for container in client.containers.list(all=True, filters={'label': ['org.distributedexec.app=DistributedExec', f'org.distributedexec.worker={self.worker_id}']}):
                container.remove(force=True)
            for path in self.root.iterdir():
                if path.is_symlink():
                    continue
                if path.is_dir() and path.resolve().parent == self.root:
                    remove_attempt(path, self.root)
        finally:
            client.close()

    def execute(self, task, stop, log=lambda *_: None, persist=lambda _: None):
        start = time.monotonic()
        client = client_for(self.endpoint)
        container = None
        path = self.root / str(uuid.UUID(task['attempt_id']))
        path.mkdir()
        input_dir, output_dir = path / 'input', path / 'output'
        input_dir.mkdir()
        output_dir.mkdir()
        os.chmod(output_dir, 0o777)
        request = encode({'function_name': task['function_name'], 'data': task['data']})
        if digest(task['code']) != task['code_hash'] or digest(encode(task['data'])) != task['input_hash']:
            raise RuntimeError('Input or code hash mismatch')
        (input_dir / 'task.py').write_text(task['code'], encoding='utf-8')
        (input_dir / 'request.json').write_text(request, encoding='utf-8')
        for file in input_dir.iterdir():
            os.chmod(file, 0o444)
        os.chmod(input_dir, 0o755)
        image = approved_image(client, self.endpoint)
        response = {'status': 'infrastructure', 'error': 'Container failed to produce output'}
        pump = None
        try:
            if stop.is_set():
                response = {'status': 'interrupted', 'error': 'Stopped before container start', 'runtime_id': image, 'duration': 0}
                persist(response)
                return response
            container = client.containers.create(image, detach=True, network_mode='none', user='65532:65532',
                read_only=True, cap_drop=['ALL'], security_opt=['no-new-privileges:true'], pids_limit=64,
                nano_cpus=int(task['cpu'] * 1e9), mem_limit=task['memory_mb'] * 1048576,
                memswap_limit=task['memory_mb'] * 1048576, init=True,
                tmpfs={'/tmp': 'rw,noexec,nosuid,size=16777216,mode=1777'}, shm_size=1048576,
                volumes={str(input_dir): {'bind': '/input', 'mode': 'ro'}, str(output_dir): {'bind': '/output', 'mode': 'rw'}},
                log_config=LogConfig(type='json-file', config={'max-size': '1m', 'max-file': '1'}),
                labels={'org.distributedexec.app': 'DistributedExec', 'org.distributedexec.worker': self.worker_id,
                        'org.distributedexec.workspace': task['workspace_id'], 'org.distributedexec.attempt': task['attempt_id']})
            stream = container.attach(stream=True, stdout=True, stderr=True, demux=True, logs=True)

            def pump_logs():
                count = 0
                try:
                    for stdout, stderr in stream:
                        for name, content in [('stdout', stdout), ('stderr', stderr)]:
                            if content and count < MAX_LOG:
                                content = content[:min(16000, MAX_LOG - count)]
                                count += len(content)
                                log(name, content.decode('utf-8', errors='replace'))
                except Exception:
                    pass
            pump = threading.Thread(target=pump_logs, daemon=True)
            pump.start()
            container.start()
            while True:
                container.reload()
                if container.status in ('exited', 'dead'):
                    break
                if stop.is_set():
                    response = {'status': 'interrupted', 'error': 'Interrupted: cancellation, stop or lease safety deadline'}
                    container.kill()
                    break
                if time.monotonic() - start > task['timeout']:
                    response = {'status': 'timeout', 'error': f"Wall-clock timeout ({task['timeout']}s)"}
                    container.kill()
                    break
                size, entries = 0, 0
                for base, directories, files in os.walk(output_dir, followlinks=False):
                    entries += len(files) + len(directories)
                    for filename in files:
                        file = Path(base) / filename
                        size += file.lstat().st_size
                    if size > 8 * 1024 * 1024 or entries > 256:
                        break
                if size > 8 * 1024 * 1024 or entries > 256:
                    response = {'status': 'invalid_result', 'error': 'Output directory watchdog limit exceeded'}
                    container.kill()
                    break
                time.sleep(0.15)
            container.wait(timeout=8)
            if pump:
                pump.join(timeout=2)
            container.reload()
            state = container.attrs['State']
            if state.get('OOMKilled'):
                response = {'status': 'oom', 'error': 'Container exceeded memory budget'}
            elif response['status'] == 'infrastructure':
                result = output_dir / 'result.json'
                if result.is_symlink() or not result.is_file() or result.stat().st_size > MAX_OUTPUT:
                    response = {'status': 'invalid_result', 'error': 'Missing, symlinked or oversized structured result'}
                else:
                    with open(result, encoding='utf-8') as f:
                        response = json.load(f)
            response.update(runtime_id=image, exit_code=state['ExitCode'], duration=time.monotonic() - start)
            persist(response)
            return response
        except Exception as exc:
            response = {'status': 'infrastructure', 'error': str(exc)[:32768], 'runtime_id': image, 'duration': time.monotonic() - start}
            persist(response)
            return response
        finally:
            if container:
                container.remove(force=True)
            if pump:
                pump.join(timeout=1)
            client.close()
            # Input files were deliberately read-only; make them removable on Windows.
            for file in input_dir.iterdir():
                os.chmod(file, stat.S_IWRITE | stat.S_IREAD)
            remove_attempt(path, self.root)
