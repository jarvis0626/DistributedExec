# DistributedExec implementation checklist

- [x] A: Inspect prototype and local changes; capture ordered result contract and empty-input defect.
- [x] B: Durable SQLite coordinator, authenticated transfer, isolated Docker execution, ordered downloads.
- [x] C: Concurrent scheduling, renewable leases, recovery, logs, revocation and cancellation.
- [x] D: Native launcher, Docker onboarding, offline browser dashboard.
- [x] E: Windows onedir build, automated acceptance checks, documentation and handoff.
- [x] F: Single Windows installer, optional automated Docker download/setup, release-ready EXE.

Milestones are committed and pushed to origin/main as requested. Version 1.2.0 also publishes the
Windows installer and checksum as a GitHub Release.

## Baseline (2026-10-05)

Checkout: d4d151c. Local host.py change uses UTF-8 for the dashboard; preserve explicit UTF-8 decoding.
No AGENTS.md found in the workspace or parent directories. GitHub remote is jarvis0626/DistributedExec.
Existing contract: a Python function consumes a JSON array; chunks aggregate in input order.
Observed: empty input divides into a zero range step; job/worker state is volatile, routes unauthenticated,
worker heartbeat stops during execution, and no lease or attempt ownership exists.
Baseline tests exercise ordered aggregation, the no-worker queue and the known empty-input failure.
Docker CLI is unavailable on this Windows development machine. Container tests must be reported as skipped.

## Milestone B verification

Docker Desktop was installed by the user after baseline capture. Its selected desktop-linux context
is used through the SDK. The bundled standard-library runtime uses a digest-pinned Python base.
`python -m pytest -q`: 21 passed, including eight real Docker integration checks on Windows.
These checks cover separate stdout/stderr, timeouts, OOM, cancellation, container hardening and cleanup.
The UTF-8 dashboard fix is preserved in distributedexec/api.py. Legacy worker/download routes are removed.

## Milestone C verification

The real two-worker failure demo killed worker 1 after its live log arrived, waited for lease expiry,
and recovered on worker 2. Output `[2,4,6,8]` contained exactly two accepted chunks, with the expired
attempt retained. The repeat run exited successfully, including Windows read-only file cleanup.
Heartbeats, live log replay and the local lease safety watchdog run in independent threads.
Output/log limits and resource-aware claims are enforced; deterministic errors fail without retry.
Additional SQLite tests exercise cancellation/completion and cancellation/reaper races.

## Milestone D verification

The Windows Qt launcher starts actual host/worker service processes and supports setup, pairing,
resource budgets, pause/resume/drain/stop, saved reconnection, simultaneous hosting/compute,
pairing rotation/revocation and diagnostics. Its widget tests use Windows Qt's offscreen platform.
Headless Edge browser tests blocked all external requests and verified locally bundled Monaco,
upload clearing, a working text fallback, queued jobs, empty output preview/download and onboarding.
`python -m pytest -q`: 30 passed. `node --check static/app.js`: passed.

## Milestone E verification

Final source suite: 34 passed, including ordinary Python module/annotation loading, FIFO timestamp ties,
Docker orphan protection and corrupted-transfer cleanup.
Pinned build dependencies, PyInstaller spec, hidden service dispatch, complete license notices and a
Windows Actions artifact workflow are included. tools/build_windows.ps1 passed its 19 host-only checks,
built the onedir distribution, and ran the packaged smoke test successfully with real Docker execution.
The benchmark checked equivalent local/distributed result equality and reported startup/transfer overhead.
Open dist/DistributedExec/DistributedExec.exe; keep the complete folder. Detailed evidence and remaining
clean-machine/manual acceptance limits are in docs/VERIFICATION.md. No release or public deployment.

## Milestone F verification

Version 1.1.0 adds a per-user setup EXE bundling Python and application libraries. The optional compute
step downloads a signature-verified official Docker installer only if needed, opens its vendor UI,
and prepares the compute runtime after Docker starts. Existing ready Docker requires no downloads.
Full suite: 44 passed. Host-only build suite: 30 passed, 14 excluded. Real Docker publisher verification
and existing-runtime reuse passed on this machine. The installer build and isolated install/reinstall/
uninstall smoke passed, including installed packaged host/worker execution and preservation of user data.
The release EXE is about 45 MiB. GitHub Actions now uploads the setup EXE and SHA256 checksum.
First-time Docker/WSL setup and restart handling remain clean-machine manual acceptance items.

## Milestone G verification

Version 1.1.1 improves onboarding, field validation, upload handling, connection recovery, progress,
retry guidance, responsive dashboard layout, launcher controls and saved-worker reconnection.
Default network selection prefers an active OS-route address and omits disconnected adapters.
Fixed a reproduced Windows state-file replacement crash using separate atomic staging files and
bounded sharing-error retries. Also fixed IPv6 host approval, Unicode byte quotas and nonfinite
numeric inputs returning HTTP 500 instead of field validation errors.

83 source tests passed in disjoint final runs: 72 non-browser, eight browser (including real Docker)
and three later network-selection regressions. Real mid-chunk failure recovery, desktop/mobile
previews, dependency checks, syntax/compilation and whitespace checks passed. Rebuilt the packaged
application and version 1.1.1 installer; packaged and installed real-Docker execution passed.
Isolated install/reinstall/uninstall preserved user data. The installer is about 45 MiB and its
SHA256 checksum was verified. See docs/VERIFICATION.md for evidence and remaining manual LAN/setup checks.

## Milestone H verification

Version 1.2.0 adds guided private connections between computers on different Wi-Fi networks through
Tailscale, optional signed vendor setup, explicit account sign-in, safe readiness/device discovery,
and expiring one-field worker invitations. The host binds only its approved private address plus
loopback; saved remote reconnect checks that connection. Provider membership remains separate from
DistributedExec pairing. Existing accounts and routing preferences are preserved.

207 tests passed in the final full run. Packaged invitation pairing and real-Docker computation
passed. Isolated installer install/upgrade/uninstall and installed Docker execution passed while
preserving the user's running app, registered installation and per-user data. The setup EXE and
adjacent SHA256 checksum were verified. Detailed evidence is recorded in docs/VERIFICATION.md.
Physical two-computer, two-network provider setup and routing remain manual acceptance items.
