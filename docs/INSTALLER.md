# Windows installer

Version 1.1.0 ships as `DistributedExec-1.1.0-windows-x64-setup.exe`, a single compressed Inno Setup installer around the bundled PyInstaller application. Users need no Python, pip, source checkout or ZIP extraction. The installer installs into `%LOCALAPPDATA%\Programs\DistributedExec`, creates a Start menu shortcut, optionally adds a desktop shortcut and registers an uninstaller. Windows x64 build 19045 or later is required for the app; Docker's own supported Windows/WSL requirements also apply for compute. ARM-native Docker setup is not supported by this x64 installer.

The optional compute task launches `DistributedExec.exe gui --setup-compute`. It is off by default, and the same action is available from the launcher at any time. A ready local Docker runtime returns immediately. Existing Docker Desktop is started and reused. If missing, the app streams the official Docker Desktop amd64 installer over HTTPS to a per-user cache, enforces a 1 GiB ceiling, validates download length and verifies Windows Authenticode status `Valid` with the `Docker Inc` organization before running it. A cached download is reverified. Incomplete, cancelled or invalid downloads are removed. Concurrent downloads are locked.

The official installer runs as `install --user` with its UI visible. No silent installation, automatic agreement acceptance, forced OS feature changes or reboot flags are passed. The user finishes Docker's installation and welcome screens. Once a local Linux daemon responds, the app prepares the digest-pinned runtime using its existing explicit setup approval path. Cancellation during the vendor wizard stops the app's wait without terminating that wizard. Setup can be resumed after Windows restarts by reopening the app and choosing Set up compute. Host-only use needs no Docker download and no first-run internet.

Install and uninstall operate on app binaries and shortcuts. Per-user workspaces, settings, runtime approvals, credentials and Docker are preserved. Updates retain user data. No login startup, firewall rule or scheduled task is created. Setup and app are unsigned; the separately downloaded Docker installer is vendor signed.

## Building and releasing

`tools/build_windows.ps1` builds/tests the application, calls `tools/build_installer.ps1`, and runs `tools/installer_smoke.py`. The compiler bootstrap uses official Inno Setup 6.7.3 with a fixed SHA256 and verifies its Pyrsys B.V. signature. The compiler is a developer build dependency only, never an end-user prerequisite. An existing installed DistributedExec causes installer smoke to refuse operation; use a clean build account instead of overwriting that installation.

The GitHub workflow uploads the installer and checksum. To create a release, download the build artifact, extract it as the release maintainer and attach the setup EXE and optional checksum to the release. Users download the EXE directly. The workflow intentionally does not create or publish releases on each push.

## Verification limits

Automated tests cover verified download caching, wrong publisher, unsigned payloads, truncation, redirect rejection, cancellation, existing ready Docker and starting/preparing existing Docker. Local build verification exercises the real installer, its registered installation path, a packaged host/worker computation, reinstall and uninstall with a marker proving per-user data preservation. CI tests installed host-only execution when Docker is unavailable. First-time Docker/WSL installation, welcome/licensing prompts, UAC, a required reboot and physical multi-computer networking still require clean-machine manual acceptance. We do not claim those environments were tested by mocked download tests.
