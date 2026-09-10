# Troubleshooting

| Symptom | Check / next step |
| --- | --- |
| `vmrun not found` | Run `doctor`; supply `--vmrun` for a nonstandard Workstation/Fusion location. On macOS it is inside the Fusion app bundle. VMware binaries are not bundled. |
| `vmrun is not executable` | On Linux/macOS, check execute permission and the installed binary's architecture. |
| VM path rejected | Supply an existing `.vmx` file, not a shortcut or virtual disk. Relative config paths are based on the config directory. |
| Tools state is not running | Confirm the selected VM is running and VMware Tools is installed and responsive inside it. The bridge will not restart either automatically. |
| Authentication fails | Use the guest account. Check its password and username inside the guest; locked accounts and Tools authentication policy can reject guest operations. |
| `serve` asks for credential configuration | `serve` never prompts. Select an already exported password variable with `password_env`; do not put the password value in TOML or JSON. |
| GUI process exists but no window appears | Use `ps --interactive` with a logged-in guest desktop. Default guest execution can use session 0. |
| PowerShell times out | Keep the returned request ID, inspect the guest, and try `result ID`. The script may still be running. `exit`, process termination, or lost Tools connectivity can prevent a result. |
| Linux paths rejected or `sh` requires POSIX | Set `guest_os = 'linux'` and an absolute private `guest_root`, with `/` separators. Host and guest OS settings are independent. |
| Shell exits with 126/127 | Check the absolute `guest_shell` path, its execute permission, and commands used by the script inside the guest. `/bin/sh` may differ from Bash. |
| Shell times out | Collect the same request with `result ID`. Check `/bin/sh`, `mv`, directory permissions, available storage, and Tools connectivity; a missing completion marker is not proof that the script never ran. |
| Linux GUI program fails | `sh` has no terminal or guaranteed desktop-session environment. Run noninteractive commands or use the configured console to interact with an existing desktop. |
| Upload fails in a nested directory | Create the guest parent first with `mkdir --path folder`. Check file permissions and available guest storage. |
| Output file already exists | Choose a new target or pass `--overwrite` intentionally. Transfers stage local output before publication. |
| Console is disabled | Follow [console setup](console.md); its RFB opt-in is separate from guest authentication. |
| Console requires unsupported authentication | This client supports only opt-in loopback RFB without authentication. Keep existing protection in place; use a compatible viewer for that VM. |
| Typed text has wrong characters | Switch the guest to English (US). Upload a UTF-8 file for Unicode text. |
| Click was sent but nothing changed | Capture the current guest screen; verify coordinates, focus, and whether the application is ready. Do not automatically repeat the action. |
| Console disconnects after VM resize/power change | Reconnect, then capture a new frame before input. Requests are not retried automatically. |
| Diagnostic text is garbled | Set `--encoding` to the code page used by your `vmrun`, such as `cp866` or `cp1251`. |

If VMware Tools itself hangs, the console channel may still work. Use it to inspect the guest before deciding whether to restart a guest service. A console failure can also be independent of Tools. Neither channel guarantees recovery from a frozen guest or a failed hypervisor.

For a bug report, include a small reproduction and versions. Redact personal paths and command lines; avoid uploading screenshots or request files containing secrets.
