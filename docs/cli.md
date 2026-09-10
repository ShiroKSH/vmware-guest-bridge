# Command reference

```text
vmb [global options] COMMAND [command options]
```

Global options go **before** the command. `vmb --help`, `vmb COMMAND --help`, and `vmb --version` work without a VM or credentials. Flags are not abbreviated. Output is plain text without color or a pager; shell completion is not bundled.

## Configuration

`vmb init --output bridge.toml` writes a starter config and refuses to overwrite an existing file. `--config FILE` explicitly loads it. No files are auto-discovered and no `.env` files are loaded.

Use `vmb --guest-os linux init` for a Linux guest; `macos`, `freebsd`, and `posix` also select POSIX paths and shell execution. The default is `windows`. See [platform setup](platforms.md) for host/guest combinations.

Precedence: command-line flag, explicit TOML config, built-in default. Config paths resolve relative to the config file; CLI paths resolve relative to the current directory. The only credential environment lookup is the exact variable selected by `password_env` / `--password-env`.

| Global flag | TOML field | Default / purpose |
| --- | --- | --- |
| `--vmx PATH` | `vmx` | Required for Tools operations; existing `.vmx` file |
| `--vmrun PATH` | `vmrun` | Host-specific Workstation/Fusion installation, then `PATH` |
| `--host-type TYPE` | `host_type` | `auto`: `fusion` on macOS, `ws` elsewhere; explicit `ws` or `fusion` supported |
| `--guest-os OS` | `guest_os` | `windows`; also `linux`, `macos`, `freebsd`, `posix` |
| `--guest-shell PATH` | `guest_shell` | `/bin/sh`; absolute interpreter path inside a POSIX guest |
| `--username USER` | `username` | Guest account; not needed for `status` or console |
| `--password-env NAME` | `password_env` | Name of an already exported password variable; otherwise hidden prompt |
| `--host-root PATH` | `host_root` | `.guest-bridge` beside the config, or in the current directory |
| `--guest-root PATH` | `guest_root` | Windows: `C:\GuestBridge`; POSIX: explicitly set an absolute private directory |
| `--timeout SECONDS` | `timeout` | `60`; allowed range 0.1–3600 |
| `--encoding NAME` | `encoding` | Host locale encoding for `vmrun` diagnostics |
| `--console-host IP` | `[console] host` | `127.0.0.1`; numeric loopback only |
| `--console-port PORT` | `[console] port` | `5900` |
| `--allow-unauthenticated` | `[console] allow_unauthenticated` | `false`; explicit RFB opt-in |
| `--json` | — | One JSON response envelope on stdout, including errors |
| `--no-input` | — | Never prompt; fail if required credentials or text are missing |

Ordinary diagnostics use the host locale; override `--encoding cp866` or another code page if needed. Scripts and PowerShell JSON use UTF-8. Shell stdout/stderr are decoded as UTF-8 with replacement for invalid bytes. `serve` accepts UTF-8 input and writes ASCII-escaped JSON.

## Commands

| Command | Effect |
| --- | --- |
| `doctor` | Check local VM/config/executable paths. No guest operation. |
| `status` | Read `checkToolsState` through `vmrun`, without guest credentials. |
| `processes` | List guest processes. The returned text can contain guest command lines. |
| `mkdir --path folder` | Create a directory and missing ancestors below `guest_root`. |
| `upload --source a.txt --target folder/b.txt` | Copy from `host_root` to `guest_root`; guest parent must exist. |
| `download --source folder/b.txt --target returned.txt` | Stage a guest file, then publish it below `host_root`. |
| `ps --file script.ps1` | Run a UTF-8 file below `host_root`, return a request ID and JSON output. |
| `ps --file app.ps1 --interactive` | Run in the logged-in guest user's desktop session. |
| `sh --file script.sh` | Run a UTF-8 file in a POSIX guest; return stdout, stderr, exit code, and request ID. |
| `result REQUEST_ID` | Collect an existing `ps` or `sh` result with the original config; never executes the script again. |
| `serve` | Run the sequential JSON-lines interface on stdin/stdout. |
| `console probe` | Read framebuffer dimensions and the server's desktop name. |
| `console screenshot --target screen.png` | Save a complete PNG below `host_root`. |
| `console key Ctrl+Alt+Delete` | Send a chord and release keys in reverse order. |
| `console type --text "Hello"` | Send ordinary ASCII text using a US keyboard mapping. |
| `console type --secret` | Read text without terminal echo, then type it into the guest. |
| `console click --x 120 --y 90 --button left` | Click coordinates relative to the guest framebuffer. |

`upload`, `download`, and `console screenshot` accept `--overwrite`. Parent host directories are created as needed. All transfer names are relative to their roots, even when those roots are absolute. `.vmb` is reserved for request records.

Host names follow host OS rules; guest names follow `guest_os`. POSIX paths are case-sensitive and use `/`; backslashes are rejected. Windows supports `/` and `\` separators and rejects drive/UNC/device paths, alternate data streams, device names, and trailing-dot/space aliases. Both reject traversal and empty path components. Use separate source/target names when transferring a POSIX filename that Windows cannot represent.

`ps` captures PowerShell pipeline output into `guest.data`, always an array. Exceptions and a nonzero final native-command exit code produce `guest.ok=false`. A literal `exit`, a killed PowerShell process, or loss of VMware Tools may prevent result creation. Scripts that invoke several native commands should check intermediate exit codes themselves.

`sh` captures `guest.stdout` and `guest.stderr` as strings, plus integer `guest.exit_code`; `guest.ok` is true only for exit code zero. It runs in `guest_root` with stdin closed and no terminal. The configured interpreter reads the script regardless of its shebang; use `--guest-shell /bin/bash` for Bash. CRLF is normalized to LF. See [shell behavior](platforms.md#shell-behavior) for exit handling, background jobs, and result limits.

The timeout covers the complete `ps`/`sh` workflow, including preparation and result polling. `result` uses one deadline for its complete collection. Each ordinary `vmrun` call has its own timeout. A console action has one transport deadline. A timeout kills the waiting host `vmrun` process, not the guest program. There is no automatic cancellation, retry, or VM restart.

## Keys

Named keys: `Ctrl`, `Alt`, `Shift`, `Win`, `Enter`, `Tab`, `Escape`, `Space`, `BackSpace`, `Delete`, `Insert`, `Up`, `Down`, `Left`, `Right`, `Home`, `End`, `PageUp`, `PageDown`, `Menu`, `Plus`, `F1`–`F12`. Names are case-insensitive. Letters and digits are also accepted. Join up to eight distinct keys with `+`.

Text typing supports printable ASCII, newlines, and tabs; CRLF is sent as one Enter. The limit is 1024 characters per action. Events are paced; increase `--timeout` for longer input. Use a file transfer for Unicode or large text. `sent=true` describes transport delivery only; observe the application before relying on its state.

## Output and exit codes

Default: results on stdout, errors on stderr. `--json`: one envelope on stdout for success or a handled error. `serve`: a ready event followed by one envelope per request. No request text is logged by the bridge.

| Exit | Meaning |
| --- | --- |
| `0` | Success; for `serve`, normal EOF or `quit` |
| `1` | Operation, file, connection, or guest-script failure |
| `2` | Invalid config/request/arguments, or an oversized protocol line |
| `3` | Outcome unknown; inspect the guest or collect the existing request |
| `130` | Interrupted; guest actions may still be running |

Request failures do not terminate `serve`; inspect each response's `ok`. An oversized line terminates it to avoid losing message boundaries. Closing the bridge releases local connections and credential references; it does not stop guest processes.
