import os
import sys
from pathlib import Path
from platformdirs import user_data_dir, user_log_dir, user_config_dir


def assets():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def data_dir():
    return Path(user_data_dir('DistributedExec', 'DistributedExec'))


def logs_dir():
    path = Path(user_log_dir('DistributedExec', 'DistributedExec'))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir():
    path = Path(user_config_dir('DistributedExec', 'DistributedExec'))
    path.mkdir(parents=True, exist_ok=True)
    return path


def private_file(path, content):
    """Use user-profile ACL inheritance on Windows, mode 0600 on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(path, 0o600)
