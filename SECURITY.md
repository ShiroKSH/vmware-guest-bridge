# Security

## Trust boundaries

The bridge runs as the host user. It accepts trusted local commands through CLI arguments or stdin, invokes an installed `vmrun` executable, and can connect to a loopback RFB server. PowerShell runs with the selected guest account's permissions. There is no remote API, user authentication service, telemetry, automatic updater, or plugin loader.

Protect these assets: the guest password, host transfer directory, guest filesystem, console input, screenshots, and stored request scripts/results. Anyone who can write commands to `serve` has the same guest control as its operator. Do not expose stdin through a network service or feed it instructions from untrusted documents.

## Credentials and stored files

- Guest passwords come from a hidden terminal prompt or an explicitly named environment variable. Plaintext password fields and password-value CLI flags are not supported.
- `vmrun` requires `-gp` in its process arguments. Administrators and sufficiently privileged local software can read it. The configured password variable is removed from the child environment; this does not remove the argument exposure.
- Raw `vmrun` diagnostics are captured in a host temporary file that is closed and deleted after the call. Treat the host's temporary directory as trusted storage too.
- The bridge redacts exact occurrences of the current guest password in response strings and object keys. This is not a general-purpose secret scanner. It cannot recognize transformed secrets, other application credentials, or secrets inside downloaded files/screenshots.
- `console type --secret` hides terminal input. The text still reaches the guest and the unauthenticated RFB transport.
- Request scripts and results remain below `host_root/.vmb` and `guest_root\.vmb`. They inherit the directories' OS permissions. Python strings are not guaranteed to be erased from memory when references are dropped.
- Do not place secrets in PowerShell source text. Use a suitable guest-side secret store when scripts need credentials. Keep both roots private and inspect artifacts before sharing them.

## File boundaries

User transfer names must be relative. The bridge rejects traversal, absolute/UNC/device paths, alternate data streams, Windows device names, and trailing-dot/space aliases. It resolves host links and rejects paths outside `host_root`. Downloads are staged before publication; existing destinations require explicit overwrite.

These checks prevent common mistakes, not attacks by a process that can mutate the host directory concurrently. Host link races, hard-link aliases, guest junctions, a malicious guest, guest resource exhaustion, and VMware vulnerabilities are outside this boundary. Use a dedicated host directory, a least-privileged guest account, and an appropriately isolated VM. PowerShell itself is not restricted to `guest_root`.

Upload existence checks are not atomic in the guest. Do not concurrently write the same guest destination. A transfer timeout can leave a partial guest file; verify content before using it.

## Console boundary

Console access is disabled by default. The client accepts numeric loopback addresses only, does not resolve hostnames, and requires explicit opt-in to unauthenticated RFB. It supports neither VNC password authentication nor TLS. A malicious local process could impersonate a console on the selected port; verify VMware owns that listener. Loopback does not isolate you from other local users or processes.

RFB reads have an overall operation deadline and bounded dimensions, rectangle counts, and payload sizes. Screenshots are published only after every pixel is covered. Input failures are not automatically retried because an event may already have reached the guest.

## Reporting a vulnerability

Use this repository's **Security → Report a vulnerability** option for a private report. If unavailable, email the maintainer at **kushidashiro@gmail.com**. Include a minimal reproduction with synthetic inputs and the affected commit or version. Do not send passwords, VM images, or private guest artifacts.

The project is in its initial development cycle; fixes target the latest `main` revision.
