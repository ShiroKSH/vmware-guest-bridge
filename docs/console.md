# Local VMware console

The bridge connects to VMware's RFB/VNC console. This is separate from the guest's virtual network adapter. The guest can have networking disabled while the host connects to a loopback console listener.

This client supports RFB 3.3, 3.7, and 3.8, raw true-color frames, and the `None` security type. It does **not** support password-authenticated VNC or TLS. Use it only on a host whose local users and processes you trust. A general VNC viewer is a better fit when you need authenticated remote access.

## Set up an opt-in local console

1. Power off your test VM normally before editing its configuration. Back up its `.vmx` file.
2. Configure these entries once, choosing a free local port. Do not create duplicate entries or remove an existing VNC password to make this client work.

   ```text
   RemoteDisplay.vnc.enabled = "TRUE"
   RemoteDisplay.vnc.ip = "127.0.0.1"
   RemoteDisplay.vnc.port = "5900"
   ```

3. Start the VM. Verify the actual listener, not just the config:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 5900 |
       Select-Object LocalAddress, LocalPort, OwningProcess
   ```

   It must be bound to `127.0.0.1`, not `0.0.0.0` or a LAN address. Use the returned PID with `Get-Process -Id PID` and confirm it belongs to the intended VMware VM process. A desktop name returned by the server is not authenticated identity. If the listener is exposed, disable it before continuing.

4. Match the port and explicitly opt in in `bridge.toml`:

   ```toml
   [console]
   host = '127.0.0.1'
   port = 5900
   allow_unauthenticated = true
   ```

5. Run `vmb --config bridge.toml console probe`, then capture a screenshot before sending input.

When finished, disable the VM's remote-display listener through its configuration while powered off and verify that the port is no longer listening. Exiting the bridge closes its client connections; it does not disable VMware's server.

The bridge intentionally does not automate these VM configuration changes. Loopback binding was exercised with Workstation 17.6.4; verify it again for your installed version.

## Input and captures

`serve` retains one shared RFB connection for keyboard/mouse actions. Before first input it receives a full frame, so initialization is complete. Each screenshot uses a separate, short-lived shared connection: Workstation can leave a later nonincremental frame request unanswered when the display is unchanged. Other viewers are not disconnected.

Screenshots contain the guest framebuffer, without the host desktop or VMware window chrome. Click coordinates use those pixels, not host display scaling. After a resolution change, capture again; the bridge drops an input session whose dimensions no longer match. Unsupported resize messages produce an error requiring reconnection.

Typing uses US keyboard mappings and supports printable ASCII, Tab, and Enter. Select English (US) **inside the guest** first. Input is not clipboard paste, and it does not check which application has focus. Secure desktop/UAC behavior depends on the guest and VMware; validate the visible screen before interacting.

The protocol has no acknowledgement that an application accepted a key or click. A lost connection may leave an uncertain action or held keys. Inspect the screen and release the relevant modifiers if necessary; do not blindly repeat a click or command.

Protocol reference: [RFC 6143](https://www.rfc-editor.org/rfc/rfc6143), including the version-specific `SecurityResult` behavior and raw framebuffer encoding.
