# Hosts and guests

The host runs Python and `vmrun`. The guest runs VMware Tools and the script. These are separate choices: selecting `guest_os = 'linux'` on Windows does not make the bridge look for a Linux host executable.

## Host platforms

| Host | VMware product | Automatic `-T` value | Installed `vmrun` location checked before `PATH` |
| --- | --- | --- | --- |
| Windows | Workstation | `ws` | `%PROGRAMFILES(X86)%\VMware\VMware Workstation\vmrun.exe` |
| Linux | Workstation | `ws` | `/usr/bin/vmrun`, then `/usr/lib/vmware/bin/vmrun` |
| macOS | Fusion | `fusion` | `/Applications/VMware Fusion.app/Contents/Library/vmrun` |

Use `--vmrun` for another installation path and `--host-type ws` or `--host-type fusion` to override product detection. On POSIX hosts, the executable must have execute permission. The CLI requires Python 3.11+ and has no runtime Python dependencies.

On Apple Silicon, Fusion runs ARM64/AArch64 guest operating systems. An x86 VM image cannot be used as an ARM guest. Broadcom lists `vmrun` for both Intel and Apple Silicon Fusion in its [feature comparison](https://knowledge.broadcom.com/external/article/315609).

## Guest profiles

| `guest_os` | Script command | Default script interpreter | Example private `guest_root` |
| --- | --- | --- | --- |
| `windows` (default) | `ps` | Windows PowerShell 5.1 | `C:\GuestBridge` |
| `linux` | `sh` | `/bin/sh` | `/home/guest-user/.guest-bridge` |
| `macos` | `sh` | `/bin/sh` | `/Users/guest-user/.guest-bridge` |
| `freebsd` | `sh` | `/bin/sh` | `/home/guest-user/.guest-bridge` |
| `posix` | `sh` | `/bin/sh` | An absolute directory owned by the guest account |

The POSIX profiles use the same implementation. They require working authenticated guest operations in VMware Tools, `/bin/sh`, and `mv`. Use a guest OS and architecture supported by your VMware product and Tools release. Profile selection supplies path and shell behavior; it does not install Tools or add hypervisor support for a new operating system. Native host CI and the recorded VM checks are described in [testing](testing.md).

File transfer, Tools status, and process listing work independently of the script interpreter. The optional RFB console depends on the VMware product exposing a compatible local listener; profile selection does not enable it. Guest desktop launch is currently available through Windows `ps --interactive`. POSIX `sh` uses a noninteractive session without an attached terminal or guaranteed graphical-session environment.

## Linux guest setup

Install the distribution's `open-vm-tools` package **inside the guest**, and ensure its system service is running. The upstream project recommends distribution packages and describes the guest-operation plugin in its [README](https://github.com/vmware/open-vm-tools). CLI operations do not require an X desktop; the separate desktop package provides GUI integration.

Examples, choosing the command for your guest distribution:

```sh
# Debian / Ubuntu
sudo apt install open-vm-tools

# Fedora
sudo dnf install open-vm-tools

# Arch Linux, with an up-to-date system
sudo pacman -S open-vm-tools
```

Package references: [Debian](https://packages.debian.org/source/stable/open-vm-tools), [Fedora](https://packages.fedoraproject.org/pkgs/open-vm-tools/open-vm-tools/index.html), [Arch Linux](https://archlinux.org/packages/extra/x86_64/open-vm-tools/). Service names and startup commands depend on the distribution; confirm the service is active before using the bridge's `status` command. A desktop package alone is not a substitute for the system Tools service.

Inside the guest, create a private directory as the account that will run commands:

```sh
install -d -m 700 "$HOME/.guest-bridge"
```

On the host, generate a config:

```sh
vmb --guest-os linux init --output bridge.linux.toml
```

Edit the placeholders. `vmx` is a **host** path; `guest_root` is a **guest** path. No home-directory or environment-variable expansion occurs in guest paths.

```toml
vmx = '/path/to/Linux-Lab/Linux-Lab.vmx'
host_type = 'auto'
guest_os = 'linux'
username = 'guest-user'
host_root = './.guest-bridge'
guest_root = '/home/guest-user/.guest-bridge'
guest_shell = '/bin/sh'
timeout = 60
```

Use a Windows `vmx` path when the host is Windows; use the `.vmx` file inside the `.vmwarevm` bundle for Fusion. Keep `guest_root` private before uploading scripts. Newly captured shell output uses mode `600`; uploaded scripts inherit VMware Tools' transfer permissions.

Create `.guest-bridge` on the host, copy `examples/hello.sh` into it, and run:

```sh
vmb --config bridge.linux.toml doctor
vmb --config bridge.linux.toml status
vmb --config bridge.linux.toml sh --file hello.sh
```

Use the same flow with `--guest-os macos`, `freebsd`, or `posix` for a compatible guest. For Linux distributions with Python older than 3.11, install a supported Python on the **host**; guest Python is not required.

## Shell behavior

`sh` reads a UTF-8 script below `host_root`, removes a UTF-8 BOM when reading a file, normalizes CRLF to LF, and uploads it. A `/bin/sh` wrapper starts the configured `guest_shell` with that file, with `guest_root` as its working directory, stdin closed, and `umask 077`. The default interpreter is `/bin/sh`; set `guest_shell = '/bin/bash'` for Bash syntax. The interpreter is an absolute path, not a command string; a file's shebang does not override it.

The wrapper captures stdout and stderr, records the interpreter's exit code, and publishes a completion marker last. A script's explicit `exit 7` produces `guest.ok=false` and `guest.exit_code=7`. Success follows the final shell exit status: use `set -e` or check intermediate commands if their failures must stop your script. Wait for background jobs before exiting if their output matters.

Collected stdout and stderr are decoded as UTF-8 with replacement for invalid bytes. Use file transfer for exact binary data. The host accepts at most 8 MiB of combined shell output. VMware copies a result file before its size is checked; these limits do not bound guest disk usage or transport volume. Keep large output in a named file and download it separately.

After a timeout, use `result REQUEST_ID` with the original VM, guest OS, and roots. The completion marker and output files stay under `.vmb`, so a later process can collect them without rerunning the script.
