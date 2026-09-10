"""Execute the generated scripts in a real /bin/sh; substitute only the Tools transport."""

import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from vmware_guest_bridge.config import Config
from vmware_guest_bridge.errors import BridgeError, InvalidRequest, OutcomeUnknown
from vmware_guest_bridge.guest import MAX_RESULT_BYTES, Guest


class LocalTools:
    def __init__(self, root):
        self.root = root
        self.processes = []

    def run(self, command, *args, **kwargs):
        if command in ("directoryExistsInGuest", "fileExistsInGuest"):
            exists = (
                Path(args[0]).is_dir()
                if command.startswith("directory")
                else Path(args[0]).is_file()
            )
            return (0 if exists else 1), ""
        if command == "createDirectoryInGuest":
            Path(args[0]).mkdir()
        elif command in ("CopyFileFromHostToGuest", "CopyFileFromGuestToHost"):
            try:
                shutil.copyfile(args[0], args[1])
            except FileNotFoundError:
                raise BridgeError("Fixture result is not ready.") from None
        elif command == "runProgramInGuest":
            if args[0] != "-noWait":
                raise AssertionError("Expected a single asynchronous script launch")
            self.processes.append(
                subprocess.Popen(
                    list(args[1:]),
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        else:
            raise AssertionError(f"Unexpected fixture command: {command}")
        return 0, ""

    def finish(self):
        for process in self.processes:
            process.wait(timeout=5)


@unittest.skipUnless(os.name == "posix" and Path("/bin/sh").is_file(), "Requires native /bin/sh")
class NativeShellTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.host_root = self.root / "host"
        self.host_root.mkdir()
        self.guest_root = self.root / "guest's $(touch injected) `touch another`"
        self.guest_root.mkdir(mode=0o700)
        self.runner = LocalTools(self.guest_root)
        self.addCleanup(self.runner.finish)
        self.config = Config(
            guest_os="linux", host_root=self.host_root, guest_root=str(self.guest_root), timeout=5
        )
        self.guest = Guest(self.config, self.runner)

    def test_stdout_stderr_unicode_and_quoted_guest_root(self):
        result = self.guest.shell("printf 'hello ✓\\n'; printf 'diagnostic\\n' >&2; pwd")
        self.assertEqual(
            result["guest"],
            {
                "ok": True,
                "exit_code": 0,
                "stdout": f"hello ✓\n{self.guest_root}\n",
                "stderr": "diagnostic\n",
            },
        )
        self.assertFalse(list(self.root.rglob("injected")))
        self.assertFalse(list(self.root.rglob("another")))
        self.assertEqual(len(self.runner.processes), 1)
        self.assertEqual(self.guest.result(result["request_id"]), result)

    def test_explicit_exit_and_syntax_failure_are_collected(self):
        for script, expected in (("printf before; exit 7; printf after", 7), ("if then\n", 2)):
            with self.subTest(script=script):
                result = self.guest.shell(script)["guest"]
                self.assertFalse(result["ok"])
                self.assertEqual(result["exit_code"], expected)
                if expected == 7:
                    self.assertEqual(result["stdout"], "before")
                else:
                    self.assertTrue(result["stderr"])

    def test_utf8_bom_and_windows_line_endings_in_uploaded_shell_file(self):
        script = self.host_root / "source.sh"
        script.write_bytes(b"\xef\xbb\xbfvalue=42\r\nprintf '%s' \"$value\"\r\n")
        result = self.guest.shell_file("source.sh")
        self.assertEqual(result["guest"]["stdout"], "42")
        self.assertTrue(result["guest"]["ok"])
        saved = self.host_root / ".vmb" / (result["request_id"] + ".sh")
        self.assertNotIn(b"\r", saved.read_bytes())
        self.assertFalse(saved.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_timeout_then_collection_never_runs_script_twice(self):
        guest = Guest(replace(self.config, timeout=0.2), self.runner)
        with self.assertRaises(OutcomeUnknown) as error:
            guest.shell("printf x >> counter; sleep 0.4; printf completed")
        request_id = error.exception.details["request_id"]
        self.assertTrue(error.exception.details["guest_result"].endswith(".exitcode"))
        self.runner.finish()
        self.assertEqual(guest.result(request_id, timeout=5)["guest"]["stdout"], "completed")
        self.assertEqual((self.guest_root / "counter").read_text(), "x")
        self.assertEqual(len(self.runner.processes), 1)

    def test_private_output_permissions_and_non_utf8_output(self):
        result = self.guest.shell("printf '\\377'")
        self.assertEqual(result["guest"]["stdout"], "\ufffd")
        for suffix in ("stdout", "stderr", "exitcode"):
            path = self.guest_root / ".vmb" / f"{result['request_id']}.{suffix}"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.host_root / ".vmb").stat().st_mode & 0o777, 0o700)

    def test_alternative_shell_path_is_quoted(self):
        shell = self.guest_root / "shell's path $(touch injected)"
        shell.symlink_to("/bin/sh")
        guest = Guest(replace(self.config, guest_os="posix", guest_shell=str(shell)), self.runner)
        self.assertEqual(guest.shell("printf custom")["guest"]["stdout"], "custom")
        self.assertFalse(list(self.root.rglob("injected")))

    @unittest.skipUnless(Path("/bin/bash").is_file(), "Requires Bash")
    def test_selected_bash_runs_bash_syntax(self):
        guest = Guest(replace(self.config, guest_shell="/bin/bash"), self.runner)
        result = guest.shell("values=(6 7); printf '%s' \"$((values[0] * values[1]))\"")
        self.assertTrue(result["guest"]["ok"])
        self.assertEqual(result["guest"]["stdout"], "42")

    def test_oversized_combined_output_is_not_published(self):
        result = self.guest.shell("printf 123456; printf 12345 >&2")
        other_host = self.root / "collector"
        guest = Guest(replace(self.config, host_root=other_host), self.runner)
        with (
            patch("vmware_guest_bridge.guest.MAX_RESULT_BYTES", 10),
            self.assertRaises(BridgeError),
        ):
            guest.result(result["request_id"])
        self.assertFalse(list((other_host / ".vmb").glob("*.exitcode")))
        self.assertFalse(list((other_host / ".vmb").glob("*.stdout")))

    def test_file_round_trip_between_separate_roots(self):
        payload = bytes(range(256))
        (self.host_root / "input.bin").write_bytes(payload)
        self.guest.mkdir("nested/Case")
        self.guest.upload("input.bin", "nested/Case/output.bin")
        self.guest.download("nested/Case/output.bin", "nested/returned.bin")
        self.assertEqual((self.host_root / "nested/returned.bin").read_bytes(), payload)
        self.assertEqual((self.guest_root / "nested/Case/output.bin").read_bytes(), payload)


class ShellCollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runner = MagicMock()
        self.config = Config(guest_os="linux", guest_root="/home/user/vmb", host_root=self.root)
        self.guest = Guest(self.config, self.runner)

    def test_bad_exit_codes_rejected_before_output_copy(self):
        for raw in (b"", b"true\n", b"-1\n", b"256\n", b"00\n", b"0\n0", b"0" * 9):

            def copy(command, source, target, raw=raw, **kwargs):
                Path(target).write_bytes(raw)
                return 0, ""

            self.runner.run.reset_mock()
            self.runner.run.side_effect = copy
            with self.subTest(raw=raw), self.assertRaises(BridgeError):
                self.guest.result("a" * 32)
            self.assertEqual(self.runner.run.call_count, 1)
        self.assertFalse((self.root / ".vmb" / ("a" * 32 + ".exitcode")).exists())

    def test_collection_uses_one_deadline_for_all_three_files(self):
        def copy(command, source, target, **kwargs):
            Path(target).write_bytes(b"0\n" if source.endswith(".exitcode") else b"")
            return 0, ""

        self.runner.run.side_effect = copy
        with patch("vmware_guest_bridge.guest.time.monotonic", side_effect=[0, 1, 2, 3]):
            self.guest.result("a" * 32, timeout=4)
        self.assertEqual(
            [call.kwargs["timeout"] for call in self.runner.run.call_args_list], [3, 2, 1]
        )

    def test_invalid_script_and_result_id_do_not_contact_guest(self):
        for script in (None, "", "\x00", "x" * (MAX_RESULT_BYTES + 1)):
            with self.subTest(script_type=type(script)), self.assertRaises(InvalidRequest):
                self.guest.shell(script)
        with self.assertRaises(InvalidRequest):
            self.guest.result("../../passwd")
        self.runner.run.assert_not_called()

    def test_launch_timeout_keeps_request_and_never_retries(self):
        def run(command, *args, **kwargs):
            if command == "runProgramInGuest":
                raise OutcomeUnknown("fixture launch timeout")
            return 0, ""

        self.runner.run.side_effect = run
        with self.assertRaises(OutcomeUnknown) as error:
            self.guest.shell("printf hello")
        request_id = error.exception.details["request_id"]
        self.assertTrue((self.root / ".vmb" / f"{request_id}.runner.sh").is_file())
        self.assertEqual(
            sum(call.args[0] == "runProgramInGuest" for call in self.runner.run.call_args_list), 1
        )
