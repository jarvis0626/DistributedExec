# DistributedExec implementation checklist

- [x] A: Inspect prototype and local changes; capture ordered result contract and empty-input defect.
- [x] B: Durable SQLite coordinator, authenticated transfer, isolated Docker execution, ordered downloads.
- [x] C: Concurrent scheduling, renewable leases, recovery, logs, revocation and cancellation.
- [ ] D: Native launcher, Docker onboarding, offline browser dashboard.
- [ ] E: Windows onedir build, automated acceptance checks, documentation and handoff.

Each major milestone is committed and pushed to origin/main as requested. No release publication.

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
