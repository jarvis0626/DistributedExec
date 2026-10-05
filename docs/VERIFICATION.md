# Verification evidence (2026-10-05)

Environment: Windows 11 x64, Python 3.12.3, Docker Desktop selected `desktop-linux` context,
Linux engine 29.8.1, WSL2. The user installed and started Docker Desktop during implementation.

- `python -m pytest -q`: **30 passed**, including real Docker, Windows Qt widget and headless Edge browser checks.
- `node --check static/app.js`: passed.
- `python -m compileall -q distributedexec host.py worker.py DistributedExec.py`: passed.
- `python tools/failure_demo.py --output local-data/failure-demo-2`: passed. Worker 1 was killed after a live
  log, its lease expired, worker 2 recovered ordered `[2,4,6,8]`, exactly two attempts succeeded and expired history remained.
- `python -m PyInstaller --noconfirm DistributedExec.spec`: built the Windows windowless onedir distribution.
- `dist/DistributedExec/DistributedExec.exe smoke`: **exit 0**, including Qt offscreen rendering, bundled assets,
  windowless packaged coordinator, authenticated browser bootstrap, durable empty result, packaged worker,
  two real Docker chunks, live logs, ordered full download and graceful service exit.

Local executable: `dist/DistributedExec/DistributedExec.exe`. The complete folder is required.
Smoke evidence is written in the per-user logs folder: smoke-report.json, launcher-smoke.png and service logs.
The repeatable failure demo's local report is deliberately gitignored because it contains local machine/workspace metadata.

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
The build is unsigned. No public deployment or release publication was performed.
