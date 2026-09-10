"""Run a small, opt-in round trip against an already running Windows or POSIX guest."""

import argparse
import hashlib
import json
import uuid

from vmware_guest_bridge.config import load_config
from vmware_guest_bridge.protocol import Bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--console", action="store_true", help="also capture the guest screen")
    args = parser.parse_args()
    bridge = Bridge(load_config(args.config))
    run_name = "smoke-" + uuid.uuid4().hex[:12]
    try:
        bridge.guest.status()
        host_folder = bridge.guest.workspace.root / run_name
        host_folder.mkdir(parents=True)
        payload = b"VMware Guest Bridge\r\nUTF-8: \xe2\x9c\x93\r\n"
        (host_folder / "input.txt").write_bytes(payload)
        bridge.guest.mkdir(run_name)
        bridge.guest.upload(f"{run_name}/input.txt", f"{run_name}/input.txt")
        bridge.guest.download(f"{run_name}/input.txt", f"{run_name}/output.txt")
        assert (host_folder / "output.txt").read_bytes() == payload, "Transfer content mismatch"
        if bridge.config.windows_guest:
            result = bridge.guest.powershell("[pscustomobject]@{message='bridge smoke'; value=6*7}")
            assert result["guest"]["ok"], "PowerShell reported an error"
            assert result["guest"]["data"][0]["value"] == 42, "PowerShell output mismatch"
        else:
            result = bridge.guest.shell("printf '%s\\n' \"$((6 * 7))\"")
            assert result["guest"]["ok"], "Guest shell reported an error"
            assert result["guest"]["stdout"] == "42\n", "Shell output mismatch"
            failure = bridge.guest.shell("printf 'expected failure\\n' >&2; exit 7")
            assert failure["guest"]["exit_code"] == 7 and not failure["guest"]["ok"]
        assert bridge.guest.result(result["request_id"])["guest"] == result["guest"]
        if args.console:
            bridge.console.screenshot(f"{run_name}/screen.png")
        print(
            json.dumps(
                {
                    "ok": True,
                    "run": run_name,
                    "round_trip_sha256": hashlib.sha256(payload).hexdigest(),
                    "guest_os": bridge.config.guest_os,
                    "script": "passed",
                    "result_collection": "passed",
                    "console_capture": "passed" if args.console else "not requested",
                }
            )
        )
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
