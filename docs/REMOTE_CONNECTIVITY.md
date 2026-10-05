# Connect across different networks

DistributedExec can connect a host and workers using different Wi-Fi networks, mobile hotspots or
different locations. The launcher uses Tailscale to establish a private connection between computers.
Tailscale is optional for computers already on the same LAN.

## One-time setup

Open the cross-network connection panel in each launcher. Select Install Tailscale to download the
official Windows installer. The app verifies the publisher before opening the vendor setup window.
Complete installation and its browser sign-in. Reuse an existing installation when available;
select Open Tailscale sign-in to connect or finish signing in.
Choose Check connection after setup or sign-in; the launcher shows readiness and available devices.

For computers you own, sign into the same Tailscale account on each computer. For another person's
computer, use Tailscale's user invitation or device-sharing controls so it is permitted to reach the
host. Use the provider's own sign-in; do not share passwords or authentication keys through this app.
The official [Windows setup guide](https://tailscale.com/docs/install/windows) explains its vendor UI.

## Host and join

On the host, choose Different networks and start the workspace once the private connection is ready.
The app selects its private address automatically. Choose Copy invitation for another computer and send it privately to
the worker. The host must remain running and internet access must stay available on both computers.

On the worker, finish compute setup if needed, paste the invitation into Join as a worker, and
select Connect. The host address and code fill automatically. Manual entry remains available. A connection invitation
expires after at most ten minutes. Ask the host for a fresh invitation if the code expires or rotates.
Existing paired workers use their saved credentials when reconnecting.

## Troubleshooting

- Missing: choose Install Tailscale, finish the vendor installer, and check again.
- Sign-in required: choose Open Tailscale sign-in and complete the provider's browser flow, then check again.
- Stopped or unavailable: start Tailscale and check its tray app/service; retry the check.
- No host listed: ensure both computers are in the same permitted private network and online. Seeing
  a device does not prove it is hosting DistributedExec; the invitation identifies the correct port.
- Cannot connect: keep the host running, verify the invitation is current, and check the provider's
  access policies and the host firewall. The app does not change those policies or firewall rules.
- Installation needs administrator approval: complete Windows' vendor setup prompts. Cancelling our
  download does not close an installer already opened under your control.
- Remote connection interruption: jobs remain in the workspace, and worker leases/retries retain the
  same behavior as LAN mode. Internet and relay latency can make small distributed jobs slower.

Tailscale provides the network connection; DistributedExec provides the host, pairing and computation.
The [device-connectivity guide](https://tailscale.com/docs/how-to/connect-to-devices) explains service
addresses, access policies and firewall checks. No public host, public tunnel or router port forwarding
is configured. Tailscale account limits and service terms are managed by that provider.

## Verification scope

Automated tests simulate missing/stopped/signed-out/ready provider states, setup cancellation and
publisher checks, invitation parsing/expiry, private-address hosting and existing LAN workflows.
They exercise app behavior without signing the developer machine into a Tailscale account. A real
two-computer, two-network connection still requires manual acceptance with authorized accounts.
