import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from vmware_guest_bridge.config import (
    GUEST_SYSTEMS,
    Config,
    example_config,
    find_vmrun,
    load_config,
    positive_timeout,
)
from vmware_guest_bridge.errors import InvalidRequest
from vmware_guest_bridge.paths import Workspace, publish_file, relative_parts, validate_guest_root


class PathTests(unittest.TestCase):
    def test_windows_path_escape_and_aliases_rejected(self):
        invalid = [
            "",
            ".",
            "..",
            "a/../b",
            "a/./b",
            "/tmp/x",
            r"\rooted",
            r"C:relative",
            r"C:\outside",
            r"\\server\share\x",
            r"\\?\C:\x",
            r"\\.\NUL",
            "file:stream",
            "NUL",
            "con.txt",
            "com1",
            "LPT9.txt",
            "COM¹.txt",
            "trailing.",
            "trailing ",
            "a//b",
            "a\x00b",
            "file*",
            ".vmb/x",
            ".VMB/x",
        ]
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(InvalidRequest):
                relative_parts(path)

    def test_unicode_and_spaces_allowed(self):
        self.assertEqual(relative_parts("отчёты/my file.txt"), ["отчёты", "my file.txt"])

    def test_guest_root_rejects_unc_and_drive_root(self):
        for root in ("relative", "C:\\", r"\\server\share\work", r"C:\work\..\elsewhere"):
            with self.subTest(root=root), self.assertRaises(InvalidRequest):
                validate_guest_root(root)

    def test_posix_paths_preserve_case_and_unix_names(self):
        self.assertEqual(
            relative_parts("reports/CON:9?.txt", windows=False), ["reports", "CON:9?.txt"]
        )
        root = validate_guest_root("/home/User's Lab", windows=False)
        self.assertEqual(str(root / "Case" / "file.txt"), "/home/User's Lab/Case/file.txt")

    def test_posix_paths_reject_traversal_and_ambiguous_separators(self):
        for value in ("", "/etc/passwd", "a/../x", "a/./x", "a//x", r"a\x", ".vmb/x", "a\x00"):
            with self.subTest(value=value), self.assertRaises(InvalidRequest):
                relative_parts(value, windows=False)
        for root in ("/", "//share/data", "/home/../tmp", "~/bridge", r"C:\GuestBridge"):
            with self.subTest(root=root), self.assertRaises(InvalidRequest):
                validate_guest_root(root, windows=False)

    @unittest.skipIf(os.name == "nt", "POSIX host filenames")
    def test_posix_host_workspace_accepts_native_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Workspace(temporary).target("CON:report?.txt")
            path.write_text("posix")
            self.assertEqual(Workspace(temporary).source("CON:report?.txt"), path)

    def test_symlink_cannot_escape_host_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Creating symlinks requires Windows Developer Mode or privilege.")
            with self.assertRaises(InvalidRequest):
                Workspace(root).target("link/escape.txt")

    def test_no_overwrite_even_if_target_appears_after_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            target = workspace.target("output.txt")
            staged = Path(temporary) / "staged"
            staged.write_text("new")
            target.write_text("keep")
            with self.assertRaises(FileExistsError):
                publish_file(staged, target)
            self.assertEqual(target.read_text(), "keep")
            publish_file(staged, target, overwrite=True)
            self.assertEqual(target.read_text(), "new")

    def test_missing_staged_file_does_not_create_empty_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                publish_file(root / "missing", root / "output")
            self.assertFalse((root / "output").exists())


class ConfigTests(unittest.TestCase):
    def test_host_auto_selection_and_explicit_override(self):
        for platform, expected in (("win32", "ws"), ("linux", "ws"), ("darwin", "fusion")):
            with (
                self.subTest(platform=platform),
                patch("vmware_guest_bridge.config.sys.platform", platform),
            ):
                self.assertEqual(Config().vmrun_type, expected)
                self.assertEqual(Config(host_type="ws").vmrun_type, "ws")
                self.assertEqual(Config(host_type="fusion").vmrun_type, "fusion")

    def test_linux_requires_explicit_private_guest_root(self):
        with self.assertRaisesRegex(InvalidRequest, "guest_root"):
            Config(guest_os="linux")
        self.assertEqual(Config().guest_root, "C:\\GuestBridge")

    def test_generated_configs_match_host_and_guest_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bridge.toml"
            for platform in ("win32", "linux", "darwin"):
                for guest_os in GUEST_SYSTEMS:
                    with (
                        self.subTest(platform=platform, guest_os=guest_os),
                        patch("vmware_guest_bridge.config.sys.platform", platform),
                    ):
                        content = example_config(guest_os)
                        path.write_text(content, encoding="utf-8")
                        config = load_config(path)
                        self.assertEqual(config.guest_os, guest_os)
                        self.assertEqual(config.windows_guest, guest_os == "windows")
                        sample_vmx = tomllib.loads(content)["vmx"]
                        self.assertEqual(sample_vmx.startswith("C:"), platform == "win32")
                        self.assertEqual(".vmwarevm/" in sample_vmx, platform == "darwin")

    def test_invalid_system_and_shell_settings_fail_early(self):
        for fields in (
            {"guest_os": "android"},
            {"host_type": "remote"},
            {"guest_shell": "sh -c"},
            {"guest_shell": "/bin/sh\nexit"},
            {"guest_shell": "/"},
            {"guest_os": "linux", "guest_root": "C:\\GuestBridge"},
        ):
            with self.subTest(fields=fields), self.assertRaises(InvalidRequest):
                Config(**fields)

    def test_vmrun_discovery_checks_native_locations_and_path(self):
        locations = (
            ("win32", "VMware/VMware Workstation/vmrun.exe"),
            ("linux", "/usr/bin/vmrun"),
            ("linux", "/usr/lib/vmware/bin/vmrun"),
            ("darwin", "/Applications/VMware Fusion.app/Contents/Library/vmrun"),
        )
        for platform, suffix in locations:
            with (
                self.subTest(platform=platform, suffix=suffix),
                patch("vmware_guest_bridge.config.sys.platform", platform),
                patch("vmware_guest_bridge.config.shutil.which", return_value=None),
                patch.object(
                    Path, "is_file", lambda path, suffix=suffix: path.as_posix().endswith(suffix)
                ),
                patch("vmware_guest_bridge.config.os.access", return_value=True),
            ):
                self.assertTrue(find_vmrun().as_posix().endswith(suffix))
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary).resolve() / "custom-vmrun"
            executable.touch(mode=0o755)
            with (
                patch("vmware_guest_bridge.config.sys.platform", "darwin"),
                patch("vmware_guest_bridge.config.shutil.which", return_value=str(executable)),
            ):
                self.assertEqual(find_vmrun(), executable)
                self.assertEqual(find_vmrun(executable), executable)

    @unittest.skipIf(os.name == "nt", "POSIX executable permission")
    def test_nonexecutable_vmrun_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "vmrun"
            executable.touch(mode=0o600)
            with self.assertRaisesRegex(InvalidRequest, "not executable"):
                find_vmrun(executable)

    def test_paths_relative_to_config_and_flags_relative_to_cwd(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.toml"
            config_path.write_text("host_root = 'files'\nvmx = 'test.vmx'\ntimeout = 20\n")
            config = load_config(config_path, timeout=5)
            expected_root = Path(temporary).resolve()
            self.assertEqual(config.host_root, expected_root / "files")
            self.assertEqual(config.vmx, expected_root / "test.vmx")
            self.assertEqual(config.timeout, 5)
            self.assertEqual(
                load_config(config_path, host_root="override").host_root, Path("override").resolve()
            )

    def test_unknown_secret_field_rejected_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("password = 'fixture-secret'\n")
            with self.assertRaises(InvalidRequest) as error:
                load_config(path)
            self.assertNotIn("fixture-secret", str(error.exception))

    def test_no_implicit_project_config(self):
        self.assertIsNone(load_config().vmx)

    def test_timeout_bounds(self):
        for value in (True, 0, -1, float("nan"), float("inf"), 3601, "3"):
            with self.subTest(value=value), self.assertRaises(InvalidRequest):
                positive_timeout(value)

    def test_loopback_numeric_only_even_with_opt_in(self):
        for host in ("localhost", "example.com", "0.0.0.0", "192.0.2.1", "::"):
            with self.subTest(host=host), self.assertRaises(InvalidRequest):
                Config(console_host=host, allow_unauthenticated=True)
        self.assertEqual(Config(console_host="::1").console_host, "::1")

    def test_invalid_console_types_rejected(self):
        for fields in (
            {"console_port": True},
            {"console_port": 65536},
            {"allow_unauthenticated": "false"},
            {"encoding": "not-a-codec"},
        ):
            with self.subTest(fields=fields), self.assertRaises(InvalidRequest):
                Config(**fields)
