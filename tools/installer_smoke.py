"""Exercise the release EXE in an isolated installation; preserve application data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import winreg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distributedexec import __version__
from distributedexec.paths import data_dir

APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{926BDF70-AB62-4B3D-8D44-C6A5F9E1C0E1}_is1'


def run(executable, arguments, timeout=180):
    result = subprocess.run([str(executable), *arguments], timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'{executable.name} exited with {result.returncode}')


def main():
    # The production AppId is intentional: also verify the real uninstall registration.
    # Refuse to replace an existing user's registered app during a build test.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            raise RuntimeError('DistributedExec is already installed. Run installer smoke on a clean test account instead.')
    except FileNotFoundError:
        pass
    project = Path(__file__).resolve().parents[1]
    test_root = (project / 'local-data').resolve()
    suffix = uuid.uuid4().hex[:8]
    destination = (test_root / f'installer-smoke-{suffix}').resolve()
    if not destination.is_relative_to(test_root) or destination.exists():
        raise RuntimeError('Unsafe installer test destination')
    installer = project / 'dist' / f'DistributedExec-{__version__}-windows-x64-setup.exe'
    marker = data_dir() / f'installer-preserve-{suffix}.txt'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('Keep user workspaces and credentials on uninstall.', encoding='utf-8')
    uninstaller = destination / 'unins000.exe'
    report = {'version': __version__, 'installer': str(installer), 'destination': str(destination)}
    options = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS=',
               f'/DIR={destination}', f'/GROUP=DistributedExec Installer Smoke {suffix}']
    try:
        run(installer, options + [f'/LOG={test_root / "installer-install.log"}'])
        report['installed'] = (destination / '_internal/static/index.html').is_file()
        if not report['installed'] or not uninstaller.is_file():
            raise RuntimeError('Installer omitted application assets or uninstall support')
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            location = winreg.QueryValueEx(key, 'InstallLocation')[0]
            if Path(location).resolve() != destination:
                raise RuntimeError('Uninstall registration points outside the test installation')
        run(destination / 'DistributedExec.exe', ['smoke'])
        report['installed_application_smoke'] = True
        run(installer, options + [f'/LOG={test_root / "installer-upgrade.log"}'])
        report['reinstall_upgrade'] = True
    finally:
        if uninstaller.is_file():
            run(uninstaller, ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                f'/LOG={test_root / "installer-uninstall.log"}'])
            deadline = time.monotonic() + 20
            while (destination / 'DistributedExec.exe').exists() and time.monotonic() < deadline:
                time.sleep(.2)
        report['uninstalled'] = not (destination / 'DistributedExec.exe').exists()
        report['user_data_preserved'] = marker.is_file() and marker.read_text(encoding='utf-8').startswith('Keep user')
        marker.unlink(missing_ok=True)
        (test_root / 'installer-smoke-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['uninstalled'] or not report['user_data_preserved']:
        raise RuntimeError('Uninstall did not clean app files or preserve user data')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
