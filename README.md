# DistributedExec

A Windows-first desktop application for running trusted Python functions across trusted computers on a private LAN. This upgrades the original DistributedExec project: the Monaco browser editor, JSON arrays and ordered chunk computation remain its core workflow.

## For normal users

1. Extract the entire **DistributedExec** Windows folder from the build artifact. Open **DistributedExec.exe**. Keep its `_internal` folder alongside it; do not copy just the executable.
2. Choose **Host a workspace**, select your LAN interface and a free port. The browser dashboard opens automatically. The launcher displays the host address and a pairing code that expires after ten minutes. **Hosting requires no Docker or Python installation.**
3. To contribute compute, install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/), choose WSL2 and Linux containers, then start Docker. For Linux use [Docker Engine](https://docs.docker.com/engine/install/). On a worker computer open DistributedExec, **Check again**, then **Prepare runtime**. The bundled standard-library runtime needs internet for its first preparation; no Git checkout is required.
4. Choose **Join as a worker**. Enter the host address and pairing code, name your computer, and choose CPU/RAM/concurrency budgets. Enable **Also contribute compute while hosting** explicitly if one computer should do both.
5. In the dashboard, choose an example, edit `def task(data): ...`, provide a JSON array or drop a JSON file, and execute. Inspect jobs, accepted progress, attempts, errors and live logs. Download the full ordered JSON result. **Clear file** restores manual dataset editing.

Docker is a separate prerequisite only for compute machines. WSL2 and hardware virtualization must work; see [Docker's WSL2 setup](https://docs.docker.com/desktop/features/wsl/). Preparation builds an approved image from a digest-pinned Python base. Monaco, its loader/fonts and notices are bundled locally. After preparation, the app and jobs can operate offline on the LAN.

The launcher supports pause-new-work, graceful disconnect, stop-now, saved-worker reconnection, pairing rotation, worker revocation, opening logs and redacted diagnostics. It owns its service processes and shuts them down deliberately. Credentials use the OS credential store when available. Configuration, logs and workspaces live in per-user directories, never the installed app folder. No auto-start on login or automatic firewall changes.

Plain HTTP is appropriate only for trusted private-LAN use and **does not encrypt credentials or data**. Docker adds isolation but is not a complete hostile-code boundary. Do not expose this service to the internet or use untrusted scripts. [Security limits](docs/SECURITY.md) explain the container controls, credential fallback and watchdog limits.

## The computation model

```python
def task(data):
    return [{"input": item, "square": item * item} for item in data]
```

A job splits an ordered array into fixed-size chunks (default 1,000 items, independent of worker count). Each attempt runs one chunk in a fresh container and must return a JSON-serializable list. Accepted outputs are combined by original chunk order. Empty input succeeds as `[]`. There is no automatic parallelization of arbitrary Python and no shared memory between chunks. Container startup/transfer costs can make small jobs slower.

Jobs and history persist in local SQLite, owned by one coordinator process. Workers transfer data through authenticated APIs. Renewable leases, bounded infrastructure retries, durable cancellation, idempotent submissions/completions/logs and strict attempt ownership protect aggregation. Execution is **at least once**; external side effects are not deduplicated. Deterministic script errors, timeouts, OOM and invalid results do not retry automatically. A manual retry creates a linked new job.

See [architecture and state transitions](docs/ARCHITECTURE.md), [clean-machine acceptance](docs/ACCEPTANCE.md), [verification evidence](docs/VERIFICATION.md), and [implementation checklist](IMPLEMENTATION.md).

## Find or build the executable

Windows build artifacts are uploaded by the repository's [Windows build workflow](https://github.com/jarvis0626/DistributedExec/actions/workflows/windows-build.yml); sign in to GitHub to download the whole onedir artifact. The workflow does not publish releases. A local build writes `dist/DistributedExec/DistributedExec.exe`.

For developers on Windows with Python 3.12, run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The script creates a build environment if needed, installs pinned dependencies, collects license notices, runs host-only checks, builds the windowless PyInstaller onedir application and smoke-tests it. This execution-policy override applies only to the build script process; normal users simply open the executable. The build is unsigned and Windows may show an OS reputation warning. Verify the source/build provenance; do not disable antivirus.

## Developer commands

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe host.py --open-browser
.\.venv\Scripts\python.exe -m distributedexec check-runtime
.\.venv\Scripts\python.exe -m distributedexec prepare-runtime
.\.venv\Scripts\python.exe -m distributedexec pair --host http://192.168.1.10:8000
# Pairing prints the profile path; run it with:
.\.venv\Scripts\python.exe worker.py --config "<profile path>"
.\.venv\Scripts\python.exe DistributedExec.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/failure_demo.py --output local-data/failure-demo
.\.venv\Scripts\python.exe example_benchmark.py --items 32 --chunk-size 16 --runs 2
```

CLI hosts listening on all interfaces should approve advertised LAN addresses using `--address <IP>`; the desktop launcher binds the selected LAN interface plus loopback. `--workspace` selects a coordinator workspace. Automation can use bearer APIs with the local owner's OS-stored secret; browser mutation clients need their session CSRF token and Origin. Secrets are never embedded in worker downloads or long-lived URLs. Insecure prototype routes/clients were removed together.

The benchmark compares the same PBKDF2 workload and verifies full result equality. End-to-end timing includes submission, queueing, container startup, transfer, aggregation and download. It distinguishes first-job from warm-service runs; runtime preparation and uncontrolled OS caches are reported separately. Two local workers test scheduling, not additional hardware capacity.

## Troubleshooting

- **Missing/stopped Docker:** hosting still works. Install/start Docker only to contribute compute, then Check again.
- **Wrong mode:** select Linux containers in Docker Desktop. Windows containers are unsupported.
- **Preparation failed:** read the displayed error/logs, check internet access and the selected local Docker context, then retry. Cancellation does not delete unrelated images/containers; a daemon-side intermediate build may continue until Docker notices the connection closed.
- **Remote Docker context:** V1 accepts only local named-pipe/Unix-socket endpoints. Select a local context before pairing; reconnect refuses to silently switch endpoints.
- **No eligible workers:** check worker runtime readiness, paused/offline status and whether available CPU/RAM can satisfy the job's reservations. Increase chunk size for very large arrays or excessive startup overhead.
- **LAN connection failure:** ensure both machines can reach the selected address/port and inspect Windows firewall rules yourself. The app never modifies firewall settings.
- **Port/workspace busy:** choose a free port or a different workspace. Existing services are never killed to claim the port/database.
- **Dashboard asks for sign-in:** use Open dashboard in the host launcher; a new local ticket refreshes the browser session.
- **Output/log budget reached:** outputs fail clearly; logs are truncated at configured limits. Workspace history is retained. Back up stopped workspaces and choose a new workspace when their content budget is exhausted.
- **Killed worker/orphan container:** restart that saved worker profile so it reconciles containers with its own application/worker labels. It never removes unrelated containers.

Use Open logs folder and Copy diagnostics for support. Do not share scripts' sensitive stdout/data. Windows per-user paths can be found in diagnostics; Linux uses the platform's standard XDG directories. [Third-party notices](licenses/THIRD-PARTY.md) accompany the application.
