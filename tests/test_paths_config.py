import tempfile
import unittest
from pathlib import Path

from vmware_guest_bridge.config import Config, load_config, positive_timeout
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
