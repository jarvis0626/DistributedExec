# Trusted private-network deployment

DistributedExec runs **trusted scripts from trusted users on a private network**. It is not a public
untrusted-code execution service. Plain HTTP does not encrypt credentials, scripts, datasets or outputs.
Do not expose the host to the internet, forward its port, or expose Docker on unauthenticated TCP.
The app never silently changes firewall settings or enables startup on login. Docker and Tailscale
setup require an explicit launcher action and leave vendor setup, sign-in and any OS prompts visible.

Version 1.2.0 adds guided Tailscale connectivity for computers on different networks. The host binds
only its approved Tailscale IPv4 address plus loopback in this mode. Application HTTP traffic between
Tailscale devices travels through the encrypted private network; HTTP on a plain LAN remains
unencrypted. Network access still depends on the user's Tailscale access policies and local firewall.
The app does not enable public sharing, exit nodes, subnet routing, port forwarding or account switching.
Tailscale remains installed and under the user's control when DistributedExec closes or is removed.

Connection invitations are bounded, expiring encodings of the host address, pairing code and expiry.
They contain no worker credential or owner secret, and should be shared privately because anyone with
network access can pair while the code is valid. They do not grant membership of a Tailscale network;
workers must join the same approved private network separately. Invitation expiry and rotation are
enforced by the coordinator as well as checked by the launcher. A changed/rotated code may fail earlier.

Worker pairing codes expire after ten minutes, can be rotated in the native launcher, and are limited to
five attempts per source IP per minute. Pairing creates unique thirty-day credentials, stored hashed by
the coordinator and revocable by the owner. Identity comes from the credential, never caller-provided
worker IDs. Attempt tokens are unique and hashed. Revocation immediately prevents API access and renewal.
Network access to the unauthenticated dashboard only displays onboarding; job/control/artifact/worker
APIs require authentication. Legacy registration, polling and Python-worker download routes are removed.

The local owner secret is stored in the OS credential store. Local browser opening uses a 60-second
single-use ticket in a URL fragment, removed by JS before bootstrap; there are no long-lived secrets in
URLs. Session cookies are HttpOnly, SameSite Strict, expire after 12 hours, and require a CSRF token plus
matching Origin for mutations. Cookies are not Secure because V1 uses local HTTP. Host headers must match
the explicitly bound/approved addresses. Request size is bounded while streaming. Artifact access is
authenticated, generated paths are based on existing database IDs, and symlinks/traversal are rejected.

OS keyring access uses Windows Credential Manager where available. On systems without a working keyring,
secrets fall back to per-user files with mode 0600. Windows chmod does not rewrite NTFS ACLs: files inherit
the user's profile ACL. Users with a permissively shared profile must correct that ACL themselves.
Restricted agent outboxes contain per-attempt tokens to support idempotent replay. Diagnostics exclude
pairing codes and credentials. Job stdout/tracebacks are authored by submitted scripts: do not print
sensitive application data and expect redaction to make it safe to share.

The prepared runtime has a digest-pinned Python base and no pip-installed job dependencies. Approval
records a built immutable image ID plus the bundled build-context hash; the agent executes by ID rather
than a mutable tag and records that ID on every completion. Docker context is explicitly displayed and
bound to the worker profile. V1 accepts only local Unix sockets/named pipes. Changing to a remote daemon
does not silently redirect execution; remote Docker contexts are unsupported.

Each chunk runs as UID/GID 65532 with CPU, memory and memory+swap limits, 64 processes, read-only root,
network disabled, all capabilities dropped, no-new-privileges and Docker's default seccomp. Only dedicated
input/output attempt paths are mounted. Job containers never receive Docker's socket, coordinator files,
worker credentials, arbitrary user-selected mounts or privileged access. A 16 MiB tmpfs and 1 MiB shared
memory bound temporary space. Container logging rotates at 1 MiB; transfer persists at most 256 KiB.
Structured files use a 4 MiB per-file limit and agent-side output directory watchdog (8 MiB or 256 entries).

The watchdog checks every ~150 ms, so a fast writer can temporarily exceed its content/entry limit. Docker
Desktop bind mounts do not provide a portable hard quota. A daemon crash can prevent cleanup; restarting
the matching worker profile reconciles its labelled orphan containers. Docker isolation is **not a complete
hostile-code boundary**; these controls do not turn restricted builtins or Python exec into a sandbox.
The runtime loads ordinary Python only inside containers, with normal standard-library functionality.
