# Testing and compatibility

## Automated checks

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m build
```

The tests exercise actual loopback TCP exchanges with a small RFB fixture server, plus a mocked `vmrun` subprocess. They cover version negotiation, fragmented handshakes, complete PNG content and CRCs, partial/malformed frames, keyboard/button release, request validation, Windows/POSIX path escapes, host-product discovery, overwrite races, credential redaction, timeouts, and result collection.

CI runs on Windows, Ubuntu, and macOS with Python 3.11, 3.13, and 3.14. Linux/macOS additionally run the generated wrappers in the native `/bin/sh`, substituting only file transport and launch for VMware Tools. These tests verify real stdout/stderr, Unicode, explicit exits and syntax failures, quoted paths, CRLF/BOM handling, private output permissions, bounded collection, byte-exact transfers, and collection after a timeout without executing twice. Native shell tests are skipped on Windows. A separate job builds and installs the wheel in a clean environment.

## Explicit guest smoke test

Prepare a disposable, already-running Windows or POSIX VM and a private `bridge.toml`. Install VMware Tools, select the guest profile, set a real guest username, and choose dedicated host/guest file roots. Follow [POSIX setup](platforms.md) for a private guest directory. Then run:

```powershell
python tools/smoke_guest.py --config bridge.toml
```

The script prompts for the guest password, creates a unique `smoke-*` directory, uploads/downloads a text fixture, compares its bytes, runs a small calculation through PowerShell or the POSIX shell, and recollects that request's result. POSIX runs also check a deliberate nonzero exit. It leaves its files for inspection. Add `--console` only after completing [console setup](console.md); this also saves a guest PNG. It does not start, stop, or reconfigure a VM.

Keep guest test files, configs, and screenshots outside version control. Clean up the unique smoke directory and request records in your own test environment when finished.

## Verified environment

The following checks were performed on 2026-09-10: byte-for-byte file round trip, PowerShell execution and error reporting, collection after an intentional timeout without rerunning the script, repeated console captures, and a WinForms input fixture. The fixture accepted ASCII text, a Shift+Home chord, and a button click, then saved the exact expected text for verification.

Environment:

| Component | Version |
| --- | --- |
| Host / guest | Windows 11 |
| VMware Workstation | 17.6.4 |
| `vmrun` | 1.17.0, build 24832109 |
| Host Python | 3.13 |
| Guest shell | Windows PowerShell 5.1 |

Check the current CI run and commit when reporting a regression. Protocol tests and the guest smoke test verify different boundaries; a green mock test does not establish compatibility with every VMware release.
