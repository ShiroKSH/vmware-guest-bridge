import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from vmware_guest_bridge.config import Config
from vmware_guest_bridge.errors import BridgeError, InvalidRequest, OutcomeUnknown
from vmware_guest_bridge.guest import Guest, wrap_script
from vmware_guest_bridge.vmrun import Credentials, Vmrun, read_secret


class VmrunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.vmx = root / "Guest with spaces.vmx"
        self.vmx.touch()
        self.executable = root / "vmrun.exe"
        self.executable.touch()
        self.config = Config(
            vmx=self.vmx,
            vmrun=self.executable,
            username="fixture-user",
            password_env="VMB_TEST_PASSWORD",
            host_root=root,
        )

    def fake_process(self, output=b"ok", exit_code=0, timeout=False):
        process = MagicMock()
        process.__enter__.return_value = process
        process.wait.side_effect = (
            [subprocess.TimeoutExpired(["never-print-argv"], 0.1), 0] if timeout else [exit_code]
        )

        def launch(args, **kwargs):
            kwargs["stdout"].write(output)
            self.assertNotIn("VMB_TEST_PASSWORD", kwargs["env"])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(args[-1], str(self.vmx))
            return process

        return process, launch

    def test_guest_password_is_redacted_and_not_in_child_environment(self):
        process, launch = self.fake_process(b"fixture-secret", exit_code=1)
        runner = Vmrun(self.config, no_input=True)
        with (
            patch.dict(os.environ, {"VMB_TEST_PASSWORD": "fixture-secret"}),
            patch("vmware_guest_bridge.vmrun.subprocess.Popen", side_effect=launch) as popen,
            self.assertRaises(BridgeError) as error,
        ):
            runner.run("listProcessesInGuest")
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertIn("[redacted]", str(error.exception))
        self.assertEqual(
            popen.call_args.args[0][3:7], ["-gu", "fixture-user", "-gp", "fixture-secret"]
        )
        self.assertEqual(runner.redact({"nested": ["fixture-secret"]}), {"nested": ["[redacted]"]})
        self.assertNotIn("fixture-secret", repr(Credentials("user", "fixture-secret")))
        self.assertFalse(process.kill.called)

    def test_timeout_kills_only_vmrun_and_reports_unknown_outcome(self):
        process, launch = self.fake_process(timeout=True)
        runner = Vmrun(self.config)
        with (
            patch("vmware_guest_bridge.vmrun.subprocess.Popen", side_effect=launch) as popen,
            self.assertRaises(OutcomeUnknown) as error,
        ):
            runner.run("checkToolsState", authenticated=False, timeout=0.1)
        self.assertNotIn("never-print-argv", str(error.exception))
        self.assertEqual(popen.call_count, 1)
        process.kill.assert_called_once()

    def test_status_does_not_prompt_for_credentials(self):
        _, launch = self.fake_process(b"running")
        runner = Vmrun(replace(self.config, username=None, password_env=None), no_input=True)
        with patch("vmware_guest_bridge.vmrun.subprocess.Popen", side_effect=launch):
            self.assertEqual(runner.run("checkToolsState", authenticated=False), (0, "running"))

    def test_legacy_encoding(self):
        _, launch = self.fake_process("готово".encode("cp866"))
        runner = Vmrun(replace(self.config, encoding="cp866"))
        with patch("vmware_guest_bridge.vmrun.subprocess.Popen", side_effect=launch):
            self.assertEqual(runner.run("checkToolsState", authenticated=False)[1], "готово")

    def test_no_input_never_falls_back_to_visible_password(self):
        with (
            patch("vmware_guest_bridge.vmrun.getpass.getpass") as prompt,
            self.assertRaises(InvalidRequest),
        ):
            read_secret("Password", no_input=True)
        prompt.assert_not_called()

    def test_missing_password_environment_is_actionable(self):
        runner = Vmrun(self.config, no_input=True)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(InvalidRequest):
            runner.credentials()

    @unittest.skipUnless(os.name == "nt", "Windows environment names are case-insensitive")
    def test_windows_password_environment_removed_regardless_of_case(self):
        _, launch = self.fake_process()
        runner = Vmrun(replace(self.config, password_env="vmb_test_password"), no_input=True)
        with (
            patch.dict(os.environ, {"VMB_TEST_PASSWORD": "fixture-secret"}),
            patch("vmware_guest_bridge.vmrun.subprocess.Popen", side_effect=launch),
        ):
            runner.run("listProcessesInGuest")

    def test_object_keys_are_redacted_too(self):
        runner = Vmrun(self.config)
        runner._credentials = Credentials("fixture-user", "fixture-secret")
        self.assertEqual(runner.redact({"fixture-secret": "value"}), {"[redacted]": "value"})


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = MagicMock()
        self.runner.run.return_value = (0, "")
        self.config = Config(host_root=self.root, timeout=0.2)
        self.guest = Guest(self.config, self.runner)

    def test_upload_refuses_existing_guest_file(self):
        (self.root / "hello.txt").write_text("hello")
        with self.assertRaises(InvalidRequest):
            self.guest.upload("hello.txt", "hello.txt")
        self.assertNotIn(
            "CopyFileFromHostToGuest", [call.args[0] for call in self.runner.run.call_args_list]
        )

    def test_download_failure_preserves_existing_file(self):
        (self.root / "output.txt").write_text("keep")

        def fail(command, source, target):
            Path(target).write_text("partial")
            raise OutcomeUnknown("copy timed out")

        self.runner.run.side_effect = fail
        with self.assertRaises(OutcomeUnknown):
            self.guest.download("input.txt", "output.txt", overwrite=True)
        self.assertEqual((self.root / "output.txt").read_text(), "keep")
        self.assertEqual([path.name for path in self.root.iterdir()], ["output.txt"])

    def test_download_success_publishes_completed_file(self):
        def copy(command, source, target):
            Path(target).write_bytes(b"round trip")
            return 0, ""

        self.runner.run.side_effect = copy
        self.assertEqual(self.guest.download("guest.txt", "nested/local.txt")["bytes"], 10)
        self.assertEqual((self.root / "nested/local.txt").read_bytes(), b"round trip")

    def test_script_quotes_guest_path_and_writes_result_atomically(self):
        script = wrap_script("'hello'", "C:\\Guest's Lab\\result.json")
        self.assertIn("'C:\\Guest''s Lab\\result.json'", script)
        self.assertIn("[IO.File]::Move", script)
        self.assertIn("$LASTEXITCODE -ne 0", script)

    def test_powershell_collects_result_and_uses_interactive_flag(self):
        def run(command, *args, **kwargs):
            if command == "CopyFileFromGuestToHost":
                Path(args[1]).write_text(json.dumps({"ok": True, "data": ["hello"]}))
            return 0, ""

        self.runner.run.side_effect = run
        response = self.guest.powershell("'hello'", interactive=True)
        self.assertEqual(response["guest"]["data"], ["hello"])
        calls = self.runner.run.call_args_list
        execution = next(call for call in calls if call.args[0] == "runProgramInGuest")
        self.assertIn("-interactive", execution.args)
        self.assertIn("-noWait", execution.args)
        self.assertEqual(sum(call.args[0] == "runProgramInGuest" for call in calls), 1)
        self.assertTrue((self.root / ".vmb" / f"{response['request_id']}.ps1").is_file())

    def test_powershell_launch_timeout_keeps_id_and_does_not_retry(self):
        def run(command, *args, **kwargs):
            if command == "runProgramInGuest":
                raise OutcomeUnknown("launch timed out")
            return 0, ""

        self.runner.run.side_effect = run
        with self.assertRaises(OutcomeUnknown) as error:
            self.guest.powershell("'hello'")
        request_id = error.exception.details["request_id"]
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")
        self.assertTrue((self.root / ".vmb" / f"{request_id}.ps1").is_file())
        self.assertEqual(
            sum(call.args[0] == "runProgramInGuest" for call in self.runner.run.call_args_list), 1
        )

    def test_result_cannot_choose_arbitrary_guest_path(self):
        with self.assertRaises(InvalidRequest):
            self.guest.result("../../secret")
        self.runner.run.assert_not_called()

    def test_malformed_result_is_not_published(self):
        def run(command, *args, **kwargs):
            Path(args[1]).write_text('{"ok":"yes"}')
            return 0, ""

        self.runner.run.side_effect = run
        with self.assertRaises(BridgeError):
            self.guest.result("a" * 32)
        self.assertFalse((self.root / ".vmb" / ("a" * 32 + ".json")).exists())
