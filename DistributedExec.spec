# PyInstaller onedir, windowless Windows application with service-mode dispatch.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
datas = [(str(root / name), name) for name in ('static', 'runtime', 'migrations', 'licenses', 'examples')]
datas += [(str(root / 'README.md'), '.'), (str(root / 'docs'), 'docs')]
hidden = collect_submodules('keyring.backends') + ['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on']
a = Analysis([str(root / 'DistributedExec.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=hidden, hookspath=[], runtime_hooks=[], excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'tkinter', 'pytest', 'playwright'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DistributedExec', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DistributedExec')
