"""Include installed third-party license texts alongside the onedir distribution."""
from pathlib import Path
from importlib.metadata import distributions
import json
import sys


def main():
    root = Path(__file__).resolve().parents[1] / 'licenses' / 'python'
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for distribution in distributions():
        name = distribution.metadata['Name']
        folder = root / (name + '-' + distribution.version)
        found = []
        for source in distribution.files or []:
            if ('licenses' in source.parts or any(word in source.name.lower() for word in ('license', 'copying', 'copyright', 'notice'))) and '.dist-info' in str(source):
                path = distribution.locate_file(source)
                if path.is_file():
                    folder.mkdir(exist_ok=True)
                    relative = Path(*source.parts[1:])
                    target = folder / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
                    found.append(str(target.relative_to(root)))
        manifest.append({'name': name, 'version': distribution.version, 'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'), 'files': found})
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        (root.parent / 'Python-PSF-LICENSE.txt').write_bytes(python_license.read_bytes())


if __name__ == '__main__':
    main()
