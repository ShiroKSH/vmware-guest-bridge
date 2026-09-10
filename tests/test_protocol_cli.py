import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vmware_guest_bridge.cli import main
from vmware_guest_bridge.config import Config, load_config
from vmware_guest_bridge.errors import InvalidRequest, OutcomeUnknown
from vmware_guest_bridge.protocol import MAX_LINE, Bridge, serve, validate_request


class ProtocolTests(unittest.TestCase):
    def bridge(self):
        bridge = MagicMock()
        bridge.runner.redact.side_effect = lambda value: value
        bridge.dispatch.return_value = {"tools_state": "running"}
        return bridge

    def test_protocol_survives_bad_json_and_correlates_requests(self):
        bridge = self.bridge()
        source = io.StringIO('bad json\n{"id":"a","action":"status"}\n{"id":2,"action":"quit"}\n')
        output = io.StringIO()
        self.assertEqual(serve(bridge, source, output), 0)
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(frames[0], {"event": "ready", "protocol": 1})
        self.assertFalse(frames[1]["ok"])
        self.assertEqual(frames[2]["id"], "a")
        self.assertEqual(frames[3]["id"], 2)
        self.assertEqual(bridge.dispatch.call_count, 2)

    def test_unknown_fields_cannot_trigger_actions(self):
        for request in (
            {"action": "status", "command": "something"},
            {"action": "upload", "source": "a", "target": "b", "overwrite": "false"},
            {"action": "ps", "script": "'a'", "file": "a.ps1"},
            {"action": "ps"},
            {"action": "sh", "script": "true", "file": "true.sh"},
            {"action": "sh"},
            {"action": "sh", "script": "true", "interactive": True},
            {"action": "reset"},
            {"action": "status", "id": []},
        ):
            with self.subTest(request=request), self.assertRaises(InvalidRequest):
                validate_request(request)

    def test_oversized_line_stops_before_dispatch(self):
        bridge = self.bridge()
        output = io.StringIO()
        self.assertEqual(serve(bridge, io.StringIO("x" * (MAX_LINE + 1)), output), 2)
        bridge.dispatch.assert_not_called()

    def test_nonfinite_json_rejected(self):
        bridge = self.bridge()
        for value in ("NaN", "Infinity", "1e999"):
            output = io.StringIO()
            serve(bridge, io.StringIO('{"action":"status","id":' + value + "}\n"), output)
            self.assertFalse(json.loads(output.getvalue().splitlines()[1])["ok"])
        bridge.dispatch.assert_not_called()

    def test_timeout_is_returned_once_with_request_id(self):
        bridge = self.bridge()
        bridge.dispatch.side_effect = OutcomeUnknown("pending", request_id="a" * 32)
        output = io.StringIO()
        serve(bridge, io.StringIO('{"id":7,"action":"ps","script":"1"}\n'), output)
        response = json.loads(output.getvalue().splitlines()[1])
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["error"]["code"], "outcome_unknown")
        self.assertEqual(response["error"]["request_id"], "a" * 32)
        self.assertEqual(bridge.dispatch.call_count, 1)

    def test_guest_script_failure_is_not_a_success_envelope(self):
        bridge = self.bridge()
        bridge.dispatch.return_value = {
            "request_id": "a" * 32,
            "guest": {"ok": False, "error": "fixture failure"},
        }
        output = io.StringIO()
        serve(bridge, io.StringIO('{"action":"ps","script":"throw 1"}\n'), output)
        self.assertFalse(json.loads(output.getvalue().splitlines()[1])["ok"])

    def test_constructing_bridge_does_not_contact_vm_or_console(self):
        with (
            patch("vmware_guest_bridge.vmrun.subprocess.Popen") as process,
            patch("vmware_guest_bridge.console.socket.create_connection") as connect,
        ):
            bridge = Bridge(Config(), no_input=True)
            bridge.close()
        process.assert_not_called()
        connect.assert_not_called()

    def test_shell_dispatch_and_nonzero_exit_envelope(self):
        bridge = Bridge(Config(guest_os="linux", guest_root="/home/user/vmb"), no_input=True)
        self.addCleanup(bridge.close)
        with patch.object(
            bridge.guest, "shell", return_value={"guest": {"ok": False, "exit_code": 7}}
        ) as shell:
            output = io.StringIO()
            serve(bridge, io.StringIO('{"id":9,"action":"sh","script":"exit 7"}\n'), output)
            response = json.loads(output.getvalue().splitlines()[1])
            self.assertFalse(response["ok"])
            self.assertEqual(response["id"], 9)
            shell.assert_called_once_with("exit 7")


class CliTests(unittest.TestCase):
    def invoke(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(args)
        return code, output.getvalue(), errors.getvalue()

    def test_init_refuses_to_overwrite_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            self.assertEqual(self.invoke(["init", "--output", str(path)])[0], 0)
            original = path.read_bytes()
            self.assertEqual(self.invoke(["init", "--output", str(path)])[0], 1)
            self.assertEqual(path.read_bytes(), original)

    def test_linux_init_and_shell_command_use_configured_guest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "linux.toml"
            code, _, _ = self.invoke(["--guest-os", "linux", "init", "--output", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(load_config(path).guest_os, "linux")
            with patch(
                "vmware_guest_bridge.guest.Guest.shell_file", return_value={"guest": {"ok": True}}
            ) as shell:
                code, _, _ = self.invoke(["--config", str(path), "sh", "--file", "hello.sh"])
            self.assertEqual(code, 0)
            shell.assert_called_once_with("hello.sh")

    def test_missing_input_json_has_exit_two_and_no_traceback(self):
        code, output, errors = self.invoke(["--json", "upload"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output)["ok"])
        self.assertEqual(errors, "")

    def test_abbreviated_flags_rejected(self):
        self.assertEqual(self.invoke(["--json", "--host-ro", "somewhere", "status"])[0], 2)

    def test_no_input_hidden_console_text_fails_before_connection(self):
        code, output, errors = self.invoke(["--json", "--no-input", "console", "type", "--secret"])
        self.assertEqual(code, 2)
        self.assertIn("terminal", json.loads(output)["error"]["message"])

    def test_cli_script_failure_exits_one(self):
        with patch(
            "vmware_guest_bridge.protocol.Bridge.dispatch",
            return_value={
                "request_id": "a" * 32,
                "guest": {"ok": False, "error": "fixture error"},
            },
        ):
            code, output, _ = self.invoke(["--json", "ps", "--file", "script.ps1"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["ok"])
