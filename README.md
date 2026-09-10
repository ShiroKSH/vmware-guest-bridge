# VMware Guest Bridge

[![CI](https://github.com/ShiroKSH/vmware-guest-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/ShiroKSH/vmware-guest-bridge/actions/workflows/ci.yml)

Run PowerShell or shell scripts, move files, and control a VMware guest from a terminal on Windows, Linux, or macOS. Guest operations use VMware Tools; screenshots, clicks, and keystrokes use VMware's local RFB console. Your host mouse and keyboard stay available while the bridge works inside the guest.

[Русский](README.ru.md) · [Platforms and Linux setup](docs/platforms.md) · [Commands](docs/cli.md) · [JSON protocol](docs/protocol.md) · [Console setup](docs/console.md)

```text
terminal / script
       |
      vmb
       +-- vmrun + VMware Tools --> PowerShell / shell / files
       +-- loopback RFB ---------> guest screen and input
```

The two channels work independently. File transfers and guest commands do not need a guest IP address, SSH, WinRM, shared folders, or clipboard sharing. Console access does not need working VMware Tools.

## Requirements

- Windows or Linux host with VMware Workstation, or macOS with VMware Fusion. The installed product must include `vmrun`.
- Python 3.11 or newer. No third-party Python packages are needed at runtime.
- A running guest with VMware Tools and a guest account. Windows uses PowerShell 5.1; Linux and other POSIX guests use `/bin/sh` and `mv`. Python is needed on the host only.
- Optional console access: a VMware RFB listener bound to a numeric loopback address. This first version supports the RFB `None` security type only, with explicit opt-in. Read [console setup](docs/console.md) before enabling it.

Host and guest systems are selected independently: Windows → Linux, Linux → Windows, and macOS → Linux use the same commands. Linux, macOS, FreeBSD, and generic POSIX guest profiles share the shell backend; the guest must also be supported by your VMware product and Tools version. See the [platform matrix](docs/platforms.md) and [verification](docs/testing.md).

The bridge does not power on VMs or change their network, sharing, firewall, or console settings. Select an existing VM explicitly.

## Install and run

On a Windows host, from PowerShell:

```powershell
git clone https://github.com/ShiroKSH/vmware-guest-bridge.git
cd vmware-guest-bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\vmb.exe init
```

You can also use GitHub's **Code → Download ZIP** button and run the last three commands from the extracted directory.

On a Linux or macOS host, for a Linux guest:

```sh
git clone https://github.com/ShiroKSH/vmware-guest-bridge.git
cd vmware-guest-bridge
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/vmb --guest-os linux init
```

`init` defaults to a Windows guest. Use `--guest-os linux`, `macos`, `freebsd`, or `posix` **before** `init` for another guest. The generated host paths and `vmrun` discovery follow the host OS.

Edit `bridge.toml`: set `vmx`, `username`, and `guest_root` for your VM. POSIX guests require an explicit absolute root in a private directory; see [guest setup](docs/platforms.md). Host paths in the config are relative to the config file. Keep passwords out of it.

Run a script in a Windows guest:

```powershell
.\.venv\Scripts\vmb.exe --config bridge.toml doctor
.\.venv\Scripts\vmb.exe --config bridge.toml status
New-Item -ItemType Directory -Path .guest-bridge -Force
Copy-Item examples\hello.ps1 .guest-bridge\hello.ps1
.\.venv\Scripts\vmb.exe --config bridge.toml ps --file hello.ps1
```

Or in a Linux/POSIX guest, from a Linux/macOS host:

```sh
.venv/bin/vmb --config bridge.toml doctor
.venv/bin/vmb --config bridge.toml status
mkdir -p .guest-bridge
cp examples/hello.sh .guest-bridge/hello.sh
.venv/bin/vmb --config bridge.toml sh --file hello.sh
```

The script command prompts for the guest password without echoing it. It returns a request ID and a result: `data` for PowerShell; `stdout`, `stderr`, and `exit_code` for shell scripts. A Windows host can also run `sh`, using its `.venv\Scripts\vmb.exe` executable and a POSIX guest config.

Commands below use `vmb` for brevity; use the full executable path above if the virtual environment is not activated.

```powershell
# Both names are relative to their configured file roots.
vmb --config bridge.toml upload --source hello.ps1 --target hello.ps1
vmb --config bridge.toml download --source hello.ps1 --target returned.ps1

# Observe the guest before sending input. Console setup is a separate step.
vmb --config bridge.toml console screenshot --target screen.png
vmb --config bridge.toml console key Ctrl+Alt+Delete
```

Existing files require `--overwrite`. Nested guest upload directories must already exist; create them with `mkdir --path folder`.

For a persistent connection, start `vmb --config bridge.toml serve` and send [JSON lines](docs/protocol.md). This mode never prompts; guest authentication uses the environment variable named by `password_env`. There is no HTTP server or listening port in the bridge itself.

## Things to know before automating

- A timeout means the guest action may still be running. For `ps` and `sh`, collect the existing request with `vmb --config bridge.toml result REQUEST_ID`, using the same config. Do not automatically resubmit a timed-out command.
- Use `ps --interactive` for programs that need the logged-in guest desktop. Ordinary guest operations run in a service session; a GUI launched there may be invisible.
- Console input reports that events were sent, not that an application accepted them. Check the screen or guest state. Typing supports ASCII and assumes the guest's US English keyboard layout.
- `vmrun` requires guest credentials in its child-process arguments. The bridge hides prompts and redacts the exact password from responses, but privileged host tools can inspect those arguments.
- Scripts and results remain in `.vmb` under both file roots for inspection after failures. Treat these directories as private. File roots prevent accidental path escapes; scripts retain the guest account's permissions.

See [SECURITY.md](SECURITY.md) for trust boundaries and [troubleshooting](docs/troubleshooting.md) for VMware Tools and console failures.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
```

Tests use a local RFB fixture server and a mocked `vmrun` process. Linux/macOS CI also executes generated scripts in a real `/bin/sh` through a local transport fixture. The separate [guest smoke test](docs/testing.md) exercises a real VM when you explicitly run it.

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). License: [MIT](LICENSE). VMware software and VM images are not included.
