"""Optional private cross-network connections through the user's Tailscale client.

Only explicit setup/sign-in actions change Tailscale's connection state. Polling
never signs in, changes accounts, enables routes, or opens a browser. Invitations
carry an existing ten-minute pairing code, never a saved worker credential.
"""
import base64
import binascii
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
import webbrowser

from filelock import FileLock, Timeout
import requests

from .paths import data_dir

WINDOWS_DOWNLOAD_URL = 'https://tailscale.com/download/windows'
INSTALLER_URL = 'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe'
MAX_INSTALLER = 128 * 1024 * 1024
STATUS_TIMEOUT = 5
LOGIN_TIMEOUT = 8
MAX_INVITATION_SECONDS = 600
INVITATION_CLOCK_SKEW = 60
TAILSCALE_IPV4 = ipaddress.ip_network('100.64.0.0/10')
PRIVATE_IPV4 = tuple(ipaddress.ip_network(value) for value in
                     ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def tailscale_executable():
    """Find an existing CLI without relying on a stale process environment."""
    located = shutil.which('tailscale')
    if located:
        return Path(located)
    # A just-installed desktop client may not be in this app's inherited PATH.
    for variable in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / 'Tailscale' / 'tailscale.exe'
            if candidate.is_file():
                return candidate
    if sys.platform == 'win32':
        candidate = Path('C:/Program Files/Tailscale/tailscale.exe')
        if candidate.is_file():
            return candidate
    return None


def _state(state, message, *, ipv4=None, network=None, devices=None):
    return {'state': state, 'message': message, 'ipv4': ipv4,
            'network': network, 'devices': devices or []}


def _run(command, timeout):
    return subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _status(executable):
    result = _run([str(executable), 'status', '--json'], STATUS_TIMEOUT)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('Tailscale status unavailable')
    status = json.loads(result.stdout)
    if not isinstance(status, dict):
        raise ValueError('Invalid Tailscale status')
    return status


def _ipv4(addresses):
    if not isinstance(addresses, list):
        return None
    for address in addresses:
        if not isinstance(address, str):
            continue
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if isinstance(parsed, ipaddress.IPv4Address) and parsed in TAILSCALE_IPV4:
            return str(parsed)
    return None


def _dns_name(value):
    if not isinstance(value, str):
        return None
    value = value.rstrip('.').lower()
    if not 1 <= len(value) <= 253 or not any(value.endswith(suffix) for suffix in
                                           ('.ts.net', '.tailscale.net')):
        return None
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
               for label in value.split('.')):
        return None
    return value


def _display_name(value):
    # These names appear in native labels; never admit markup or control codes.
    if not isinstance(value, str):
        return None
    value = re.sub(r'[^A-Za-z0-9 ._@-]', '', value).strip()[:100]
    return value or None


def _network_status(status):
    backend = status.get('BackendState')
    if backend == 'NeedsLogin':
        return _state('login_required', 'Sign in to Tailscale using the same account on both computers.')
    if backend == 'NeedsMachineAuth':
        return _state('login_required', 'This computer needs approval in the Tailscale admin console. '
                      'Approve it, then check the connection again.')
    if backend != 'Running':
        return _state('stopped', 'Tailscale is disconnected or starting. Open Tailscale or select Sign in / connect, '
                      'then check again.')
    self_status = status.get('Self')
    self_status = self_status if isinstance(self_status, dict) else {}
    address = _ipv4(status.get('TailscaleIPs')) or _ipv4(self_status.get('TailscaleIPs'))
    if not address or self_status.get('Online') is False:
        return _state('stopped', 'Tailscale is waiting for a network connection. Check your internet and try again.')
    tailnet = status.get('CurrentTailnet')
    tailnet = tailnet if isinstance(tailnet, dict) else {}
    network = _dns_name(tailnet.get('MagicDNSSuffix')) or _dns_name(status.get('MagicDNSSuffix'))
    # Deliberately omit User/LoginName, keys, AuthURL, endpoints and raw errors.
    peers = status.get('Peer')
    devices = []
    if isinstance(peers, dict):
        for peer in peers.values():
            if not isinstance(peer, dict) or peer.get('Online') is not True:
                continue
            peer_ip = _ipv4(peer.get('TailscaleIPs'))
            if not peer_ip or peer_ip == address:
                continue
            dns = _dns_name(peer.get('DNSName'))
            name = _display_name(peer.get('HostName')) or (dns.split('.')[0] if dns else peer_ip)
            devices.append({'name': name, 'ipv4': peer_ip, 'dns_name': dns})
    devices.sort(key=lambda device: (device['name'].lower(), device['ipv4']))
    return _state('ready', 'Private connection is ready. Computers in this Tailscale network can connect '
                  'from different Wi-Fi networks.', ipv4=address, network=network, devices=devices)


def check_network():
    """Return a small, safe view of the optional Tailscale connection."""
    executable = tailscale_executable()
    if not executable:
        return _state('missing', 'Set up the private connection on both computers to use different Wi-Fi networks.')
    try:
        return _network_status(_status(executable))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return _state('stopped', 'Cannot reach Tailscale yet. Open Tailscale from the system tray, '
                      'finish its setup, then check again.')


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise RuntimeError('Private connection setup cancelled. You can resume with Set up private connection.')


def verify_installer(path):
    """Require Windows to trust the downloaded executable and its vendor."""
    script = "$s = Get-AuthenticodeSignature -LiteralPath $env:DISTRIBUTEDEXEC_VERIFY_FILE; " \
             "@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env={**os.environ, 'DISTRIBUTEDEXEC_VERIFY_FILE': str(path.resolve())},
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Cannot verify the Tailscale installer signature. Nothing was installed.') from None
    try:
        signature = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('Cannot verify the Tailscale installer signature. Nothing was installed.') from None
    subject = signature.get('subject') if isinstance(signature, dict) else None
    if (not isinstance(subject, str) or result.returncode or signature.get('status') != 'Valid'
            or not re.search(r'(?:^|,\s*)O=Tailscale Inc\.?\s*(?:,|$)', subject)):
        raise RuntimeError('Tailscale installer has no valid Tailscale Inc signature. Nothing was installed.')


def download_installer(progress=None, cancel=None, root=None):
    """Cache the signed vendor wizard; download failures never leave an executable."""
    progress = progress or (lambda _: None)
    root = Path(root) if root is not None else data_dir() / 'setup' / 'tailscale'
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'Tailscale Setup.exe'
    try:
        with FileLock(root / 'download.lock', timeout=0):
            _cancelled(cancel)
            if target.is_file():
                progress('Checking the downloaded Tailscale installer...')
                try:
                    verify_installer(target)
                    return target
                except RuntimeError:
                    target.unlink()
            partial = target.with_suffix('.part')
            progress('Downloading the private connection installer from tailscale.com...')
            deadline = time.monotonic() + 300
            try:
                with requests.get(INSTALLER_URL, stream=True, timeout=(10, 30)) as response:
                    response.raise_for_status()
                    url = urlsplit(response.url)
                    if url.scheme != 'https' or url.hostname != 'pkgs.tailscale.com' or url.username or url.password:
                        raise RuntimeError('Unexpected Tailscale download location. Nothing was installed.')
                    total = int(response.headers.get('Content-Length', 0))
                    if not 0 <= total <= MAX_INSTALLER:
                        raise RuntimeError('Tailscale download exceeds the installer size limit.')
                    received, last = 0, 0
                    with partial.open('wb') as output:
                        for block in response.iter_content(1024 * 1024):
                            _cancelled(cancel)
                            if time.monotonic() > deadline:
                                raise RuntimeError('Tailscale download timed out. Select Set up private connection to retry.')
                            received += len(block)
                            if received > MAX_INSTALLER:
                                raise RuntimeError('Tailscale download exceeds the installer size limit.')
                            output.write(block)
                            if time.monotonic() - last >= .5:
                                progress(f'Downloading Tailscale: {received // 1048576} MiB' +
                                         (f' / {total // 1048576} MiB' if total else ''))
                                last = time.monotonic()
                    if not received or (total and received != total):
                        raise RuntimeError('Incomplete Tailscale download. Select Set up private connection to retry.')
                _cancelled(cancel)
                progress('Verifying the Tailscale Inc digital signature...')
                verify_installer(partial)
                _cancelled(cancel)
                partial.replace(target)
                return target
            finally:
                partial.unlink(missing_ok=True)
    except Timeout:
        raise RuntimeError('Private connection setup is already running. Wait for it to finish.') from None


def setup_network(progress=None, cancel=None):
    """Open the normal vendor wizard on Windows; leave its choices to the user."""
    _cancelled(cancel)
    status = check_network()
    if status['state'] == 'ready':
        return status
    if status['state'] != 'missing':
        _cancelled(cancel)
        return login_network()
    if sys.platform != 'win32':
        if not webbrowser.open('https://tailscale.com/download'):
            raise RuntimeError('Open https://tailscale.com/download to install Tailscale on this computer.')
        return _state('missing', 'Install Tailscale from the download page, then sign in with the same account '
                      'on both computers and check again.')
    installer = download_installer(progress, cancel)
    _cancelled(cancel)
    if progress:
        progress('Complete the Tailscale setup window. Windows may ask you to allow installation.')
    try:
        # ShellExecute presents the normal UAC/vendor wizard instead of requiring
        # elevation for DistributedExec or silently accepting vendor terms.
        os.startfile(str(installer), 'open')
    except OSError:
        raise RuntimeError('Cannot open the Tailscale installer. Select Set up private connection to retry.') from None
    return _state('missing', 'The Tailscale installer is open. Finish installation, sign in with the same '
                  'account on both computers, then select Check connection.')


def _login_url(output):
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    if not isinstance(output, str):
        return None
    for candidate in re.findall(r'https://[^\s<>"\x27]+', output[:65536]):
        try:
            parsed = urlsplit(candidate)
            if (parsed.scheme == 'https' and parsed.hostname == 'login.tailscale.com'
                    and parsed.netloc == 'login.tailscale.com' and not parsed.query and not parsed.fragment
                    and re.fullmatch(r'/a/[A-Za-z0-9]+', parsed.path)):
                return candidate
        except ValueError:
            pass
    return None


def login_network():
    """Connect the active account and open only the vendor's device sign-in URL."""
    executable = tailscale_executable()
    if not executable:
        return _state('missing', 'Install Tailscale first using Set up private connection.')
    try:
        status = _status(executable)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return _state('stopped', 'Open Tailscale from the system tray and finish its setup, then check again.')
    state = _network_status(status)
    if state['state'] == 'ready':
        return state
    if status.get('BackendState') == 'NeedsMachineAuth':
        webbrowser.open('https://login.tailscale.com/admin/machines')
        return state
    # A bare `up` preserves preferences. `login` switches profiles; --reset,
    # --force-reauth, routes and exit-node flags would disturb existing settings.
    try:
        result = _run([str(executable), 'up'], LOGIN_TIMEOUT)
        output = (result.stdout or '') + '\n' + (result.stderr or '')
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b'')
        if isinstance(output, bytes):
            output = output.decode('utf-8', errors='replace')
        error = exc.stderr or ''
        if isinstance(error, bytes):
            error = error.decode('utf-8', errors='replace')
        output += '\n' + error
    except (OSError, subprocess.SubprocessError):
        return _state('stopped', 'Open Tailscale from the system tray and select Connect or Log in, then check again.')
    url = _login_url(output) or _login_url(status.get('AuthURL'))
    if url:
        if not webbrowser.open(url):
            return _state('login_required', 'Open Tailscale from the system tray and select Log in, '
                          'then check the connection again.')
        return _state('login_required', 'Finish the Tailscale sign-in in your browser using the same account '
                      'on both computers, then select Check connection.')
    state = check_network()
    if state['state'] != 'ready':
        state['message'] = 'Open Tailscale from the system tray and select Connect or Log in, then check again.'
    return state


def _invitation_values(address, code, expires, now, *, max_seconds=MAX_INVITATION_SECONDS):
    if not isinstance(address, str) or not 1 <= len(address) <= 300 or any(
            char.isspace() or ord(char) < 32 for char in address) or '\\' in address:
        raise ValueError('Invitation has an invalid host address.')
    try:
        url = urlsplit(address)
        port = url.port
    except ValueError:
        raise ValueError('Invitation has an invalid host address.') from None
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None
            or url.password is not None or url.path or url.query or url.fragment
            or address != f'{url.scheme}://{url.netloc}' or url.netloc.endswith(':')
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('Invitation must contain a host URL without a path or credentials.')
    try:
        host = ipaddress.ip_address(url.hostname)
    except ValueError:
        if url.hostname != 'localhost' and not _dns_name(url.hostname):
            raise ValueError('Invitation must use a private network or Tailscale host address.') from None
    else:
        if not isinstance(host, ipaddress.IPv4Address) or not (
                host in TAILSCALE_IPV4 or any(host in subnet for subnet in PRIVATE_IPV4)):
            raise ValueError('Invitation must use a private network or Tailscale host address.')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Fa-f0-9]{10}', code):
        raise ValueError('Invitation has an invalid pairing code.')
    if (isinstance(expires, bool) or not isinstance(expires, (int, float))
            or not math.isfinite(expires)):
        raise ValueError('Invitation has an invalid expiry.')
    if expires <= now:
        raise ValueError('This invitation has expired. Ask the host for a new invitation.')
    if expires > now + max_seconds:
        raise ValueError('Invitation expiry exceeds ten minutes. Ask the host for a new invitation.')
    return {'address': address, 'code': code.upper(), 'expires': expires}


def encode_invitation(address, code, expires, *, now=None):
    """Create a portable invitation for the host's existing short-lived code."""
    values = _invitation_values(address, code, expires, time.time() if now is None else now)
    payload = json.dumps(values, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return 'DE1.' + base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate invitation field')
        result[key] = value
    return result


def decode_invitation(value, *, now=None):
    """Validate pasted untrusted input before it can reach a network request."""
    if not isinstance(value, str):
        raise ValueError('Paste a DistributedExec invitation beginning with DE1.')
    value = value.strip()
    if len(value) > 1024 or not re.fullmatch(r'DE1\.[A-Za-z0-9_-]+', value):
        raise ValueError('Paste a valid DistributedExec invitation beginning with DE1.')
    encoded = value[4:]
    try:
        payload = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        values = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError('This invitation is damaged. Copy a new invitation from the host.') from None
    if not isinstance(values, dict) or set(values) != {'address', 'code', 'expires'}:
        raise ValueError('Invitation has invalid fields. Copy a new invitation from the host.')
    # Allow a receiving computer's clock to run slightly behind the host. The
    # host remains authoritative and refuses the real pairing code after ten minutes.
    return _invitation_values(values['address'], values['code'], values['expires'],
                              time.time() if now is None else now,
                              max_seconds=MAX_INVITATION_SECONDS + INVITATION_CLOCK_SKEW)
