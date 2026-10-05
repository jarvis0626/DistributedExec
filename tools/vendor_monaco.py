"""Reproduce the bundled Monaco AMD assets from the official npm package."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import requests

VERSION = '0.52.2'
ROOT = Path(__file__).resolve().parents[1]


def main():
    metadata = requests.get(f'https://registry.npmjs.org/monaco-editor/{VERSION}', timeout=30)
    metadata.raise_for_status()
    dist = metadata.json()['dist']
    response = requests.get(dist['tarball'], timeout=60)
    response.raise_for_status()
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(response.content).digest()).decode()
    if integrity != dist['integrity']:
        raise RuntimeError('Monaco integrity verification failed')
    target = ROOT / 'static/vendor/monaco'
    target.mkdir(parents=True, exist_ok=True)
    notices = ROOT / 'licenses'
    notices.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:gz') as archive:
        for member in archive:
            if member.name.startswith('package/min/vs/') and member.isfile():
                relative = Path(member.name.removeprefix('package/min/'))
                path = target / relative
                if path.resolve().is_relative_to(target.resolve()):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
            elif member.name in ('package/LICENSE', 'package/ThirdPartyNotices.txt'):
                (notices / ('Monaco-' + Path(member.name).name)).write_bytes(archive.extractfile(member).read())
    (target / 'provenance.json').write_text(json.dumps({'package': 'monaco-editor', 'version': VERSION, **dist}, indent=2), encoding='utf-8')
    print(f'Verified and bundled Monaco {VERSION}: {integrity}')


if __name__ == '__main__':
    main()
