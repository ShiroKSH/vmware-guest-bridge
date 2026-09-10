# JSON-lines protocol, version 1

Start `vmb --config bridge.toml serve`. The bridge uses stdin/stdout only and keeps its input-console connection open between requests. It performs actions sequentially.

This mode never prompts. Set `password_env` in the config to the name of a credential variable already supplied by your secret manager or parent process. Console-only requests do not need a guest username/password. Do not pass credentials as request fields.

```json
{"event":"ready","protocol":1}
```

The ready event means the parser is ready; it does not validate VMware Tools or connect to the console. Send `doctor`, `status`, or `console.probe` as appropriate.

```json
{"id":"calc","action":"ps","script":"[pscustomobject]@{answer=6*7}"}
```

```json
{"id":"calc","ok":true,"result":{"request_id":"0123456789abcdef0123456789abcdef","guest":{"ok":true,"data":[{"answer":42}]}}}
```

The example request ID above is illustrative; actual IDs are random 32-character lowercase hex strings. The caller's optional `id` is separate: null, a string up to 128 characters, or an integer within JavaScript's safe integer range. Invalid envelopes receive `id:null`.

## Actions

| `action` | Required fields | Optional fields |
| --- | --- | --- |
| `doctor`, `status`, `processes` | — | — |
| `mkdir` | `path` | — |
| `upload`, `download` | `source`, `target` | `overwrite` (boolean, false) |
| `ps` | Exactly one of `script` or `file` | `interactive` (boolean, false) |
| `result` | `request_id` | — |
| `console.probe` | — | — |
| `console.screenshot` | `target` ending in `.png` | `overwrite` (boolean, false) |
| `console.key` | `key` | — |
| `console.type` | `text` | — |
| `console.click` | `x`, `y` (integers) | `button` (`left`, `middle`, `right`) |
| `quit` | — | — |

All actions accept `id`. Unknown actions/fields and wrong boolean types are rejected before dispatch. File names are relative to the configured roots. There is no action that changes host paths, credentials, console endpoints, VM power state, or networking.

## Errors and recovery

```json
{"id":"calc","ok":false,"error":{"code":"outcome_unknown","message":"No complete PowerShell result before the deadline. Use result with this request_id; do not resubmit the script.","request_id":"0123456789abcdef0123456789abcdef","guest_result":"C:\\GuestBridge\\.vmb\\0123456789abcdef0123456789abcdef.json"}}
```

Use the same generated ID with `result`. A failed collection request does not rerun anything. A failure returned by the script uses `ok:false` with `result.guest.ok:false`; transport and validation failures use an `error` object instead.

```json
{"id":"collect","action":"result","request_id":"0123456789abcdef0123456789abcdef"}
{"id":"stop","action":"quit"}
```

`quit` returns `{"closed":true}` in `result`, then exits. EOF also closes the bridge. Guest commands already started keep running.

## Limits

- UTF-8 input, one JSON object per line, at most 1 MiB per line including its newline.
- Duplicate keys, nonfinite numbers, and nesting beyond 64 levels are rejected.
- PowerShell source: 256 KiB. Collected JSON: 8 MiB. `vmrun` diagnostic response: 1 MiB, then a truncation marker.
- Console: at most 8192 pixels per dimension and 16,777,216 pixels total; at most 4096 rectangles and 1024 server messages per capture.
- Input is trusted local control. This is a small line protocol, not JSON-RPC 2.0 or an authenticated network service.

## From Python

Assume the parent environment already contains the guest password variable selected in `bridge.toml`:

```python
import json
import subprocess

with subprocess.Popen(
    ["vmb", "--config", "bridge.toml", "serve"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
    encoding="utf-8",
) as bridge:
    print(json.loads(bridge.stdout.readline()))  # ready
    bridge.stdin.write(json.dumps({"id": 1, "action": "status"}) + "\n")
    bridge.stdin.flush()
    print(json.loads(bridge.stdout.readline()))
    bridge.stdin.write(json.dumps({"id": 2, "action": "quit"}) + "\n")
    bridge.stdin.flush()
    print(json.loads(bridge.stdout.readline()))
```

For production automation, bound reads in your parent process and inspect `ok` before advancing. Never retry state-changing actions solely because a response was lost.
