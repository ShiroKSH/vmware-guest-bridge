# Contributing

For a bug, include the exact command, host and guest OS/architecture, Workstation or Fusion version, VMware Tools version, Python version, and a minimal expected/actual result. For shell commands, include `guest_os` and `guest_shell`. Replace account names and paths before sharing logs. Do not attach credentials, VM images, memory dumps, or screenshots containing private information.

Use Python 3.11-compatible code. The runtime currently uses the standard library only. Discuss a new runtime dependency or protocol change in an issue before implementing it.

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
```

Add a focused regression test for a behavioral change. RFB parsing changes should cover fragmented reads and invalid server messages; guest command changes should cover a timeout with an unknown outcome. Include only checks you actually ran in the pull request.

CI never starts a VM. Linux/macOS jobs execute the generated POSIX wrappers in a real shell through a local transport fixture; Windows jobs retain the PowerShell transport tests. See [guest smoke testing](docs/testing.md) for integration checks against your own VM. Keep those credentials and artifacts outside Git.

For a security issue, use the private reporting route in [SECURITY.md](SECURITY.md).
