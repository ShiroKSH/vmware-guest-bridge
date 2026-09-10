# VMware Guest Bridge

Run PowerShell, move files, and control a Windows VM from a terminal. Guest operations use VMware Tools; screenshots, clicks, and keystrokes use VMware's local RFB console. Your host mouse and keyboard stay available while the bridge works inside the guest.

[Русский](README.ru.md) · [Commands](docs/cli.md) · [JSON protocol](docs/protocol.md) · [Console setup](docs/console.md)

```text
terminal / script
       |
      vmb
       +-- vmrun + VMware Tools --> PowerShell and files
       +-- loopback RFB ---------> guest screen and input
```

The two channels work independently. File transfers and guest commands do not need a guest IP address, SSH, WinRM, shared folders, or clipboard sharing. Console access does not need working VMware Tools.

## Requirements

- Windows host with VMware Workstation and its `vmrun.exe` installed.
- Python 3.11 or newer. No third-party Python packages are needed at runtime.
- A running Windows guest with Windows PowerShell 5.1. VMware Tools and a guest account are required for commands and file transfers.
- Optional console access: a VMware RFB listener bound to a numeric loopback address. This first version supports the RFB `None` security type only, with explicit opt-in. Read [console setup](docs/console.md) before enabling it.

The bridge does not power on VMs or change their network, sharing, firewall, or console settings. Select an existing VM explicitly. See [compatibility and verification](docs/testing.md) for the tested versions.

## Install and run

Download or clone this repository, then run these commands from its directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\vmb.exe init
```

Edit `bridge.toml`: set `vmx` to your VM's configuration file and `username` to its Windows account. Paths in the config are relative to the config file. Keep passwords out of it.

```powershell
.\.venv\Scripts\vmb.exe --config bridge.toml doctor
.\.venv\Scripts\vmb.exe --config bridge.toml status
New-Item -ItemType Directory -Path .guest-bridge -Force
Copy-Item examples\hello.ps1 .guest-bridge\hello.ps1
.\.venv\Scripts\vmb.exe --config bridge.toml ps --file hello.ps1
```

The last command prompts for the guest password without echoing it. It returns a request ID and the script's JSON result. Commands below use `vmb` for brevity; use the full executable path above if the virtual environment is not activated.

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

- A timeout means the guest action may still be running. For PowerShell, collect the existing request with `vmb --config bridge.toml result REQUEST_ID`. Do not automatically resubmit a timed-out command.
- Use `ps --interactive` for programs that need the logged-in guest desktop. Ordinary guest operations run in a service session; a GUI launched there may be invisible.
- Console input reports that events were sent, not that an application accepted them. Check the screen or guest state. Typing supports ASCII and assumes the guest's US English keyboard layout.
- `vmrun` requires guest credentials in its child-process arguments. The bridge hides prompts and redacts the exact password from responses, but privileged host tools can inspect those arguments.
- PowerShell scripts and results remain in `.vmb` under both file roots for inspection after failures. Treat these directories as private. File roots prevent accidental path escapes; they do not sandbox PowerShell or a hostile guest.

See [SECURITY.md](SECURITY.md) for trust boundaries and [troubleshooting](docs/troubleshooting.md) for VMware Tools and console failures.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
```

Tests use a local RFB fixture server and a mocked `vmrun` process. The separate [guest smoke test](docs/testing.md) exercises a real VM when you explicitly run it.

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). License: [MIT](LICENSE). VMware software and VM images are not included.
