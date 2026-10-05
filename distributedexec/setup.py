"""Optional Windows compute bootstrap; Docker remains a vendor-managed install."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from filelock import FileLock
from .paths import data_dir
from .runtime import check_runtime, prepare_runtime

DOCKER_URL = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
MAX_INSTALLER = 1024 * 1024 * 1024


def desktop_executable():
    for root in (Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop',
                 Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker'):
        path = root / 'Docker Desktop.exe'
        if path.is_file():
            return path
    return None


def cancelled(cancel):
    if cancel.is_set():
        raise RuntimeError('Compute setup cancelled. You can resume with Set up compute.')


def verify_installer(path):
    # Pass the path through the environment, never through PowerShell source interpolation.
    script = "$s = Get-AuthenticodeSignature -LiteralPath $env:DISTRIBUTEDEXEC_VERIFY_FILE; " \
             "@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env={**os.environ, 'DISTRIBUTEDEXEC_VERIFY_FILE': str(path.resolve())},
        capture_output=True, text=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        signature = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('Cannot verify the Docker installer signature. Nothing was installed.') from None
    if result.returncode or signature.get('status') != 'Valid' or not re.search(
            r'(?:^|,\s*)O=Docker Inc\.?\s*(?:,|$)', signature.get('subject', '')):
        raise RuntimeError('Docker installer has no valid Docker Inc signature. Nothing was installed.')


def download_installer(progress, cancel, root=None):
    root = Path(root or data_dir() / 'setup')
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'Docker Desktop Installer.exe'
    with FileLock(root / 'download.lock', timeout=0):
        cancelled(cancel)
        if target.is_file():
            progress('Checking previously downloaded Docker installer…')
            try:
                verify_installer(target)
                return target
            except RuntimeError:
                target.unlink()
        partial = target.with_suffix('.part')
        progress('Downloading Docker Desktop from docker.com…')
        try:
            with requests.get(DOCKER_URL, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                # A redirect must stay on Docker's HTTPS download service.
                from urllib.parse import urlsplit
                url = urlsplit(response.url)
                if url.scheme != 'https' or url.hostname != 'desktop.docker.com':
                    raise RuntimeError('Unexpected Docker download location')
                total = int(response.headers.get('Content-Length', 0))
                if total > MAX_INSTALLER:
                    raise RuntimeError('Docker download exceeds the installer size limit')
                received, last = 0, 0
                with partial.open('wb') as output:
                    for block in response.iter_content(1024 * 1024):
                        cancelled(cancel)
                        received += len(block)
                        if received > MAX_INSTALLER:
                            raise RuntimeError('Docker download exceeds the installer size limit')
                        output.write(block)
                        if time.monotonic() - last >= .5:
                            progress(f'Downloading Docker: {received // 1048576} MiB' +
                                     (f' / {total // 1048576} MiB' if total else ''))
                            last = time.monotonic()
                if not received or (total and received != total):
                    raise RuntimeError('Incomplete Docker download. Select Set up compute to retry.')
            cancelled(cancel)
            progress('Verifying Docker Inc digital signature…')
            verify_installer(partial)
            cancelled(cancel)
            partial.replace(target)
            return target
        finally:
            partial.unlink(missing_ok=True)


def setup_compute(progress, cancel):
    if os.name != 'nt':
        raise RuntimeError('Automatic setup is available on Windows. Install Docker Engine on Linux.')
    cancelled(cancel)
    state = check_runtime()
    if state['state'] == 'failed':
        raise RuntimeError('Resolve the selected Docker context before setup: ' + state['message'])
    if state['state'] == 'ready':
        progress('Compute is already ready. No downloads needed.')
        return state
    desktop = desktop_executable()
    if not desktop and state['state'] not in ('preparing runtime', 'wrong mode'):
        installer = download_installer(progress, cancel)
        cancelled(cancel)
        progress('Complete the Docker setup window. Windows may request WSL setup or a restart.')
        process = subprocess.Popen([str(installer), 'install', '--user'])
        deadline = time.monotonic() + 1800
        while process.poll() is None:
            # Cancelling our wait deliberately leaves the vendor wizard under the user's control.
            cancelled(cancel)
            if time.monotonic() >= deadline:
                raise RuntimeError('Docker setup is still open. Finish it, then select Set up compute again.')
            cancel.wait(.5)
        if process.returncode != 0:
            raise RuntimeError(f'Docker setup exited with code {process.returncode}. Finish any required restart, then retry.')
        desktop = desktop_executable()
        if not desktop:
            raise RuntimeError('Docker installation is not ready. Finish setup or restart Windows, then retry.')
    cancelled(cancel)
    if desktop and state['state'] != 'preparing runtime':
        subprocess.Popen([str(desktop)])
    progress('Waiting for Docker. Complete its welcome screens and select Linux containers if requested…')
    deadline = time.monotonic() + 240
    while True:
        cancelled(cancel)
        state = check_runtime()
        if state['state'] == 'ready':
            return state
        if state['state'] == 'preparing runtime':
            progress('Preparing the compute runtime. This first download can take several minutes…')
            prepare_runtime(state['endpoint'], progress, cancel)
            return check_runtime()
        if state['state'] == 'wrong mode':
            raise RuntimeError('Select Linux containers in Docker Desktop, then Set up compute again.')
        if time.monotonic() >= deadline:
            raise RuntimeError('Docker is not ready yet. Finish its setup or required Windows restart, then select Set up compute again. ' + state['message'])
        cancel.wait(2)
