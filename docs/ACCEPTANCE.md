# Clean-machine acceptance

1. On a clean Windows 11 x64 account, open the single DistributedExec setup EXE. Confirm per-user install,
   Start menu shortcut and optional desktop shortcut. Launch it without installing Python or pip.
2. Without Docker running, select Host a workspace. Confirm dashboard opens, the expiring code/address
   appear, an empty-array submission succeeds with downloadable `[]`, and non-empty jobs show a queued reason.
3. On compute machines, use Set up compute. With Docker missing, verify download progress, cancellation,
   publisher verification and the official vendor setup window. Finish WSL2/Linux-container setup,
   vendor welcome screens and any Windows restart, then resume setup. Existing Docker must be reused.
4. Confirm automatic runtime preparation with internet once; verify progress/error/cancellation handling. Disconnect internet
   afterwards, restart the app, and verify local Monaco and the prepared runtime remain usable.
5. Enter host address/code, computer name and CPU/RAM/concurrency budgets. Submit a transformation with
   several chunks, inspect real reservations, logs and attempt ownership, download the full ordered result.
6. Pause new work while a current chunk continues; resume. Disconnect after work finishes. Reconnect saved
   credentials. Stop now during long_running and inspect interruption/reassignment. Enable the explicit
   simultaneous host/compute toggle on the host if desired.
7. Rotate pairing, reject the old code, revoke a worker, and confirm its credential/results are rejected.
8. Start a second host on the same workspace and/or occupied port; confirm the existing host stays alive.
9. Cancel a running job; confirm surviving restarts, stopped renewal and rejected late output. Retry as a
   linked new job. Close the launcher and verify its service processes exit and owned containers are removed.
10. Check Open logs folder and Copy diagnostics. Never submit untrusted scripts or expose the host publicly.
11. Reinstall/upgrade and uninstall. Confirm app files/shortcuts are removed but workspaces, settings,
    credentials and Docker remain. Test the launch-with-compute installer checkbox on a fresh account.

## Repeatable developer checks

`python -m pytest -q` covers real SQLite transactions and, when available, the prepared Docker daemon,
Windows Qt widget controls and headless browser asset/UI behavior. Browser tests use installed Edge on
Windows; set `DISTRIBUTEDEXEC_BROWSER` for another installed Chromium executable. Docker tests skip when
the approved image is absent; Qt worker tests are excluded with `-m 'not docker'` for host-only CI.

`python tools/failure_demo.py --output local-data/failure-demo` starts one temporary host and two local
worker agents, kills worker 1 mid-chunk after a live log, waits for lease expiry, and asserts two accepted
chunks with ordered `[2,4,6,8]`. It retains an expired attempt and saves report.json. Two local agents
demonstrate scheduling/recovery, not extra physical computing capacity. The demo cleans its labelled
containers in its finalizer, including the killed agent's orphan.

`DistributedExec.exe smoke` checks bundled assets, renders the Qt launcher using the offscreen Windows
platform, starts a windowless packaged host, verifies auth/bootstrap/empty download, and, when a runtime
is prepared, starts the packaged worker and verifies Docker output/logs/ordered download. Reports and a
launcher screenshot go to the per-user logs directory. This is automated smoke coverage, not a claim
that every interactive GUI flow or clean-machine configuration has been manually verified.

The GitHub Actions workflow builds/uploads a setup EXE and checksum and verifies installation/reinstall/
uninstall; it does not publish a release. Docker is
not available on the hosted Windows runner, so real container checks need a Docker-enabled Windows/Linux
machine. Local test results and build evidence are recorded in IMPLEMENTATION.md and docs/VERIFICATION.md.
