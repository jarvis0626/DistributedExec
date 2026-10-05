# DistributedExec

A Windows-first desktop application for running trusted Python functions across trusted computers on a private network. Computers can share a LAN or connect from different Wi-Fi networks through the guided Tailscale connection. The Monaco browser editor, JSON arrays and ordered chunk computation remain its core workflow.

## For normal users

1. Download and open [**DistributedExec-1.2.0-windows-x64-setup.exe**](https://github.com/jarvis0626/DistributedExec/releases/download/v1.2.0/DistributedExec-1.2.0-windows-x64-setup.exe). It installs the app for your Windows account, including Python and all application libraries, and creates a Start menu shortcut. No ZIP extraction, Python or pip setup is needed.
2. Choose **Host a workspace**, confirm the automatically selected active LAN interface and choose a free port. The browser dashboard opens automatically. The launcher displays the host address and a pairing code that expires after ten minutes. **Hosting requires no Docker or Python installation.**
3. To contribute compute, select **Set up compute** in the installer or launcher. It reuses existing Docker; otherwise it downloads Docker Desktop from its official HTTPS service, verifies Docker Inc's Windows signature and opens the vendor installer. Complete Docker's welcome/setup screens; Windows may need WSL2, hardware virtualization enabled or a restart. The app then prepares its compute runtime automatically. Cancel setup stops our download/wait; an already-open Docker installer remains under your control. For Linux use [Docker Engine](https://docs.docker.com/engine/install/) and **Prepare runtime**. First compute setup needs internet; no Git checkout is required.
4. Choose **Join as a worker**. Paste a connection invitation from the host, or enter the host address and pairing code manually. Name your computer and choose CPU/RAM/concurrency budgets. Enable **Also contribute compute while hosting** explicitly if one computer should do both.
5. In the dashboard, choose an example, edit `def task(data): ...`, provide a JSON array or drop a JSON file, and execute. Inspect jobs, accepted progress, attempts, errors and live logs. Download the full ordered JSON result. **Clear file** restores manual dataset editing.

Docker is downloaded only for compute machines that need it; its installer is cached for retries and signature-checked again before reuse. Its own terms and Windows setup remain visible; the app does not accept agreements or restart Windows automatically. WSL2 and hardware virtualization must work; see [Docker's WSL2 setup](https://docs.docker.com/desktop/features/wsl/). Preparation builds an approved image from a digest-pinned Python base. Monaco, its loader/fonts and notices are bundled locally. After preparation, the app and jobs can operate offline on the LAN. Uninstall removes application files and shortcuts while preserving your workspaces, settings, credentials and Docker.

The launcher supports pause-new-work, graceful disconnect, stop-now, saved-worker reconnection, pairing rotation, worker revocation, opening logs and redacted diagnostics. It owns its service processes and shuts them down deliberately. Credentials use the OS credential store when available. Configuration, logs and workspaces live in per-user directories, never the installed app folder. No auto-start on login or automatic firewall changes.

For a first job, keep the Transformation example and the eight sample numbers. Connect a ready worker, then choose Distribute & execute. The result contains each number and its square. An empty array can be used to check hosting and result downloads without a worker. Saved worker connections remain available after reopening the launcher or hosting a different workspace; Reconnect saved uses the original resource budgets.

The dashboard previews item/chunk counts and explains invalid inputs, waiting jobs and unavailable workers. If the host connection drops, job actions are disabled until it returns. Reopening the dashboard from the launcher refreshes an expired browser session. Retrying a failed or cancelled job uses its original code, dataset and settings; submit a new job to use edits.

Plain HTTP on a LAN **does not encrypt credentials or data**. Across-network mode carries application traffic inside Tailscale's encrypted private connection and binds the host to its Tailscale address plus local loopback. Docker adds isolation but is not a complete hostile-code boundary. Use trusted computers and scripts. [Security limits](docs/SECURITY.md) explain the container controls, credential fallback and watchdog limits.

## Connect computers on different Wi-Fi networks

1. In the launcher, choose **Different networks** or open the cross-network setup panel on a worker.
2. Choose **Install Tailscale** on each computer if needed. On Windows the app downloads Tailscale from its official service, verifies its Tailscale Inc. signature, and opens the vendor installer. Finish its setup and use **Open Tailscale sign-in** if prompted. For your own computers, use the same Tailscale account; invite another person into the same private network using Tailscale's account controls. Passwords and Tailscale authentication keys are never shared in a DistributedExec invitation.
3. Choose **Check connection**. Once ready, start the host in Different networks mode. Keep the host running.
4. On the host, choose **Copy invitation for another computer**. Send that invitation privately to the worker. On the worker, paste it into the invitation field; the address and code fill automatically. Select **Connect** when compute and the private connection are ready. Invitations contain the host address and a pairing code valid for at most ten minutes.

No router port forwarding is required. Both computers need internet and permission to communicate within the same Tailscale private network. Local LAN mode still works without Tailscale. See [connection setup and troubleshooting](docs/REMOTE_CONNECTIVITY.md), [Tailscale Windows setup](https://tailscale.com/docs/install/windows), and [Tailscale device connectivity](https://tailscale.com/docs/how-to/connect-to-devices).

## The computation model

```python
def task(data):
    return [{"input": item, "square": item * item} for item in data]
```

A job splits an ordered array into fixed-size chunks (default 1,000 items, independent of worker count). Each attempt runs one chunk in a fresh container and must return a JSON-serializable list. Accepted outputs are combined by original chunk order. Empty input succeeds as `[]`. There is no automatic parallelization of arbitrary Python and no shared memory between chunks. Container startup/transfer costs can make small jobs slower.

Jobs and history persist in local SQLite, owned by one coordinator process. Workers transfer data through authenticated APIs. Renewable leases, bounded infrastructure retries, durable cancellation, idempotent submissions/completions/logs and strict attempt ownership protect aggregation. Execution is **at least once**; external side effects are not deduplicated. Deterministic script errors, timeouts, OOM and invalid results do not retry automatically. A manual retry creates a linked new job.

See [architecture and state transitions](docs/ARCHITECTURE.md), [clean-machine acceptance](docs/ACCEPTANCE.md), [verification evidence](docs/VERIFICATION.md), and [implementation checklist](IMPLEMENTATION.md).

## Find or build the executable

The repository's [Windows build workflow](https://github.com/jarvis0626/DistributedExec/actions/workflows/windows-build.yml) uploads **DistributedExec-windows-installer**, containing the single setup EXE and its SHA256 checksum. GitHub wraps workflow artifacts in ZIPs for developers; attach the **setup EXE itself** to a GitHub Release so users download and run that one file. The workflow does not publish releases automatically. A local build writes `dist/DistributedExec-1.2.0-windows-x64-setup.exe` and its `.sha256` file; the intermediate app folder remains at `dist/DistributedExec/`.

For developers on Windows with Python 3.12, run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The script creates a build environment if needed, installs pinned dependencies, collects license notices, runs host-only checks, builds and smoke-tests the windowless PyInstaller application, then provisions the checksum-pinned, signature-verified Inno Setup 6.7.3 build tool. It compiles a compressed per-user installer and verifies installation, packaged execution, reinstall and uninstall in a temporary directory. This installer smoke requires an account without an existing installed DistributedExec so it cannot overwrite a user's app. See [installer design and verification](docs/INSTALLER.md). This execution-policy override applies only to the build script process; normal users simply open the setup executable. The app installer is unsigned and Windows may show an OS reputation warning. Verify the source/build provenance; do not disable antivirus.

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

- **Missing/stopped Docker:** hosting still works. Select Set up compute to download/install or start Docker and prepare the runtime. Finish any vendor screens/restart, then select it again if needed.
- **Docker download/signature failure:** setup installs nothing. Check internet access and Windows certificate/time settings, then retry Set up compute. Setup help links to Docker's official instructions.
- **Wrong mode:** select Linux containers in Docker Desktop. Windows containers are unsupported.
- **Preparation failed:** read the displayed error/logs, check internet access and the selected local Docker context, then retry. Cancellation does not delete unrelated images/containers; a daemon-side intermediate build may continue until Docker notices the connection closed.
- **Remote Docker context:** V1 accepts only local named-pipe/Unix-socket endpoints. Select a local context before pairing; reconnect refuses to silently switch endpoints.
- **No eligible workers:** check worker runtime readiness, paused/offline status and whether available CPU/RAM can satisfy the job's reservations. Increase chunk size for very large arrays or excessive startup overhead.
- **LAN connection failure:** ensure both machines can reach the selected address/port and inspect Windows firewall rules yourself. The app never modifies firewall settings.
- **Pairing failed:** copy a current code from the host launcher. If too many pairing attempts were made, wait one minute before trying again. Entering a new code does not bypass that temporary limit.
- **Port/workspace busy:** choose a free port or a different workspace. Existing services are never killed to claim the port/database.
- **Dashboard asks for sign-in:** use Open dashboard in the host launcher; a new local ticket refreshes the browser session.
- **Output/log budget reached:** outputs fail clearly; logs are truncated at configured limits. Workspace history is retained. Back up stopped workspaces and choose a new workspace when their content budget is exhausted.
- **Killed worker/orphan container:** restart that saved worker profile so it reconciles containers with its own application/worker labels. It never removes unrelated containers.

Use Open logs folder and Copy diagnostics for support. Do not share scripts' sensitive stdout/data. Windows per-user paths can be found in diagnostics; Linux uses the platform's standard XDG directories. [Third-party notices](licenses/THIRD-PARTY.md) accompany the application.
