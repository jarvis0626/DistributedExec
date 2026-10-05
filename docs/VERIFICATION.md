# Verification evidence (2026-10-05)

## Version 1.2.0 cross-network connections

- Final full source suite: **207 passed** in 110.31s, including real local Docker, Qt services and Edge
  browser computation. No tests skipped. JavaScript syntax, Python compilation, dependency consistency
  and diff whitespace checks also passed.
- PyInstaller built the windowless 1.2.0 application. Packaged smoke exited 0: native launcher,
  browser authentication, durable empty result, bundled invitation encode/decode, a real Docker
  worker, two computed chunks, live logs and ordered result download. Bundled dashboard assets,
  runtime runner, README and connection guide matched source byte for byte.
- Inno Setup compiled `DistributedExec-1.2.0-windows-x64-setup.exe`: 46,879,287 bytes. Its adjacent
  checksum matches SHA256 `f7dcd3168a1f2355408dc39b17ff70415fe03d55821f6113c36acad80992b71d`.
- Isolated installer lifecycle passed: install, installed real-Docker smoke, reinstall/upgrade,
  uninstall and user-data preservation. Because the user's app was installed and running, the test
  compiled the same app bundle/installer logic with a separate AppId, names and paths, and disabled
  closing applications. Production installer/source, the existing installed executable/registration
  and both existing app processes were verified unchanged. Evidence:
  `local-data/isolated-installer-smoke-report.json`. The production AppId remains unchanged from the
  earlier installer lifecycle verification; this run did not execute production setup over the user app.

The optional launcher panel guides Tailscale installation and sign-in on each computer, detects
readiness and online devices, and binds a different-network host to its approved Tailscale address
plus loopback. An expiring connection invitation automatically fills the worker's address and
pairing code. Saved remote-worker reconnect checks the saved host's private connection first.
Existing LAN operation and compute controls are preserved.

Provider state, setup, sign-in and account-dependent behavior use mocks; no Tailscale installation
or account login was performed on the development machine. The official Windows installer was
downloaded for inspection only. Its actual Authenticode status was Valid with O=Tailscale Inc.;
the app's publisher verification also passed against that file. Expanded Qt panel previews fit
660x480 without horizontal scrolling and used the Windows Segoe UI font.

A first full run passed 196 tests but exposed a real Docker Desktop container-list timeout during
worker startup cleanup. The worker exited before starting heartbeats. A bounded retry addresses
that transport failure while preserving owned-container filters and making permanent failures
visible. The regression checks and packaged acceptance results are recorded with this version.

Physical two-computer connections on different networks, authorized provider installation/sign-in,
and firewall/access-policy acceptance remain manual checks in docs/ACCEPTANCE.md.

## Version 1.1.1 usability and reliability update

- Final source coverage was verified in disjoint runs: **72 non-browser tests passed**, **8 browser
  tests passed** (including a real Docker worker), and **3 additional network-interface selection
  regressions passed**. Total: **83 passed**. The network-selection change was made after the main
  non-browser run and verified separately.
- Browser checks cover invalid JSON/resources and overflowing numbers, upload replacement/clearing,
  queued-job guidance, cancellation/retry with the original payload, connection loss and sign-in
  recovery, local Monaco/fallback editors, mobile layout and ordered result download. The real-worker
  UI check ran two concurrent Docker chunks, observed live logs and downloaded `[2,4,6,8]`.
- Launcher checks cover preserved saved profiles, actionable pairing/network/rate-limit errors,
  setup cancellation, state-dependent controls and real pause/resume/drain/reconnect/stop operations.
  Active network selection avoids disconnected adapters and prefers the OS route address without
  sending network payloads. Offscreen previews with the Windows Segoe UI font verified the launcher
  at 660x480 without horizontal scrolling; the dashboard also fit a 390-pixel mobile viewport.
- A real launcher test exposed a Windows sharing error while replacing `state.json`. Atomic writes
  now use separate staging files, retry brief Windows sharing conflicts and preserve the previous
  complete file on failure. Six regressions failed before this fix and passed afterwards; the final
  real worker-control and browser-computation checks also passed.
- API regressions verify IPv6 loopback approval and structured HTTP 422 responses for nonfinite
  numbers. Unicode logs/events now count UTF-8 bytes against storage/log quotas.
- Real mid-chunk worker-kill recovery passed: ordered `[2,4,6,8]`, two accepted chunks and retained
  expired-attempt history. Evidence: `local-data/usability-recovery-20261005/report.json`.
- JavaScript syntax, Python compilation, dependency consistency and diff whitespace checks passed.
- Rebuilt the windowless application with PyInstaller and compiled the version 1.1.1 installer.
  Bundled dashboard assets, runtime runner and README matched the tested source byte for byte.
  Packaged smoke exited 0, including a real Docker worker, two chunks, live logs and ordered output.
- Isolated installer lifecycle passed: installation, installed application smoke, reinstall,
  uninstall and preservation of per-user data. Evidence: `local-data/installer-smoke-report.json`.
- Installer: `dist/DistributedExec-1.1.1-windows-x64-setup.exe`, 46,864,467 bytes (about 45 MiB).
  Its adjacent checksum was verified against SHA256
  `b13f21668828eb4a0ba041daac6e9876bab95cd4fdee5f6ba0b43cb124514bd0`.

First-time Docker/WSL setup and physical multi-computer LAN checks remain manual acceptance items.

## Version 1.1.0 installer update

- Full source suite: **44 passed**; host-only build suite: **30 passed**, 14 excluded.
- `tools/build_windows.ps1`: passed bundled application build/smoke, verified Inno compiler bootstrap,
  installer compilation and isolated install/reinstall/uninstall smoke.
- Single installer: `dist/DistributedExec-1.1.0-windows-x64-setup.exe`, 46,850,265 bytes (about 45 MiB),
  with adjacent SHA256 checksum.
- Installed EXE smoke executed the packaged host and worker with the real Linux Docker runtime.
- The uninstall test verified removal of installed app files and preservation of per-user data.
- Docker bootstrap verified the actual Docker Inc Authenticode signature on the previously downloaded
  vendor installer. Running Set up compute reused this machine's approved Docker without any download.
- Mocked download tests cover caching, truncation, cancellation, unsigned payloads, wrong publishers,
  redirect rejection and starting/preparing an existing stopped Docker installation.
- First-time Docker/WSL installation, welcome screens, UAC/reboots and physical LAN use remain manual
  checks on a clean machine. The app installer is unsigned; Windows reputation prompts may appear.

## Version 1.0 baseline evidence

Environment: Windows 11 x64, Python 3.12.3, Docker Desktop selected `desktop-linux` context,
Linux engine 29.8.1, WSL2. The user installed and started Docker Desktop during implementation.

- `python -m pytest -q`: **34 passed**, including real Docker, ordinary Python module/annotation loading,
  Windows Qt widget and headless Edge browser checks.
- `node --check static/app.js`: passed.
- After the final queued-state recovery adjustment, 12 targeted SQLite/race/Docker-ordering checks passed.
- `python -m compileall -q distributedexec host.py worker.py DistributedExec.py`: passed.
- `python tools/failure_demo.py --output local-data/failure-demo-2`: passed. Worker 1 was killed after a live
  log, its lease expired, worker 2 recovered ordered `[2,4,6,8]`, exactly two attempts succeeded and expired history remained.
- `python -m PyInstaller --noconfirm DistributedExec.spec`: built the Windows windowless onedir distribution.
- `powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1`: passed dependency installation/checks,
  license collection, 19 host-only acceptance tests, Windows onedir build and packaged smoke check.
- `dist/DistributedExec/DistributedExec.exe smoke`: **exit 0**, including Qt offscreen rendering, bundled assets,
  windowless packaged coordinator, authenticated browser bootstrap, durable empty result, packaged worker,
  two real Docker chunks, live logs, ordered full download and graceful service exit.
- Equivalent eight-item benchmark on a temporary coordinator/two-concurrent-slot worker: local 0.965s,
  first-job 2.936s and warm-service 3.074s end-to-end. Both distributed outputs equalled the local output.
  This small workload was slower distributed; these sample timings are not a speedup promise or controlled-cache benchmark.

Local executable: `dist/DistributedExec/DistributedExec.exe`. The complete folder is required.
Smoke evidence is written in the per-user logs folder: smoke-report.json, launcher-smoke.png and service logs.
The repeatable failure demo's local report is deliberately gitignored because it contains local machine/workspace metadata.

The first GitHub Actions run exposed a prototype repository issue: `.venv` was tracked with a
machine-specific `C:\Python312` interpreter path. The environment was removed from Git tracking
without deleting the local files. Fresh CI builds now create their environment with the interpreter
selected by actions/setup-python on PATH; the build script also accepts an explicit `-Python` path.

## Coverage and limitations

SQLite checks cover empty/invalid input, concurrent ownership, CPU/RAM/concurrency reservations, priority,
ordered aggregation, duplicate submissions/completions/logs, lease renewal/expiry, bounded retries,
ownership, credential expiry/revocation, durable restart, cancellation/completion and cancellation/reaper
races, deterministic failure persistence, output limits, CSRF/Host/Origin, symlink and artifact access.
Real Docker checks exercise success, stdout/stderr, script exception, invalid results, timeout, memory kill,
resource hardening, cancellation and cleanup. Browser checks block external requests and exercise locally
bundled Monaco, text fallback, upload clearing, empty download, queued work and unauthenticated onboarding.
Qt tests start real services and exercise host-only mode, duplicate host rejection, host+worker, pause,
resume, drain, saved reconnect and stop. Native widget tests use the offscreen platform on Windows.

No separate clean Windows machine or two physical LAN computers were available. Interactive first-run Docker
installation, firewall behavior, port-conflict dialogs on another machine, Windows reputation warnings and
full manual native GUI acceptance remain clean-machine acceptance steps. Linux is supported by source/runtime
paths but was not tested here. Container storage watchdog/content budgets are not hard disk quotas.
The application build is unsigned. Earlier milestone runs did not publish releases; version 1.2.0
publication is covered by its verification entry above.
