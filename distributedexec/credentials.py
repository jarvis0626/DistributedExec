"""OS keyring first; restricted per-user files when a keyring is unavailable."""
import secrets
from .paths import config_dir, private_file
from .store import digest


def save(name, value):
    try:
        import keyring
        keyring.set_password('DistributedExec', name, value)
        return 'OS credential store'
    except Exception:
        private_file(config_dir() / (digest(name) + '.secret'), value)
        return 'Restricted file (inherits user-profile ACL on Windows)'


def load(name):
    try:
        import keyring
        value = keyring.get_password('DistributedExec', name)
        if value:
            return value
    except Exception:
        pass
    path = config_dir() / (digest(name) + '.secret')
    return path.read_text(encoding='utf-8') if path.exists() else None


def admin_secret(workspace):
    name = 'admin:' + str(workspace.resolve())
    value = load(name)
    if not value:
        value = secrets.token_urlsafe(32)
        save(name, value)
    return value
