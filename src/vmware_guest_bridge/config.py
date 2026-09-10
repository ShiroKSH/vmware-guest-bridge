import codecs
import ipaddress
import math
import os
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import InvalidRequest
from .paths import Workspace, validate_guest_root, validate_posix_path

GUEST_SYSTEMS = ("windows", "linux", "macos", "freebsd", "posix")
HOST_TYPES = ("auto", "ws", "fusion")


def example_config(guest_os="windows", host_type="auto"):
    if sys.platform == "win32":
        vmx = r"C:\VMs\Guest\Guest.vmx"
    elif sys.platform == "darwin":
        vmx = "/path/to/Guest.vmwarevm/Guest.vmx"
    else:
        vmx = "/path/to/Guest/Guest.vmx"
    guest_root = (
        r"C:\GuestBridge"
        if guest_os == "windows"
        else "/Users/guest-user/.guest-bridge"
        if guest_os == "macos"
        else "/home/guest-user/.guest-bridge"
    )
    return f"""# Edit vmx, username, and guest_root. No passwords belong here.
# Host paths are relative to this file unless absolute.
vmx = '{vmx}'
host_type = '{host_type}'
guest_os = '{guest_os}'
username = 'guest-user'
host_root = './.guest-bridge'
guest_root = '{guest_root}'
timeout = 60
# guest_shell = '/bin/sh'            # POSIX guests; /bin/bash is also supported.
# vmrun = '/absolute/path/to/vmrun'  # Optional override of automatic discovery.
# password_env = 'VMB_GUEST_PASSWORD'  # Name of an already exported variable.
# encoding = 'utf-8'                 # Override if vmrun uses a legacy code page.

[console]
host = '127.0.0.1'
port = 5900
allow_unauthenticated = false
"""


def positive_timeout(value):
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidRequest("timeout must be a number of seconds.")
    if not math.isfinite(value) or not 0.1 <= value <= 3600:
        raise InvalidRequest("timeout must be between 0.1 and 3600 seconds.")
    return float(value)


@dataclass(frozen=True)
class Config:
    vmx: Path | None = None
    vmrun: Path | None = None
    host_type: str = "auto"
    guest_os: str = "windows"
    guest_shell: str = "/bin/sh"
    username: str | None = None
    password_env: str | None = None
    host_root: Path = Path(".guest-bridge")
    guest_root: str | None = None
    timeout: float = 60
    encoding: str | None = None
    console_host: str = "127.0.0.1"
    console_port: int = 5900
    allow_unauthenticated: bool = False

    def __post_init__(self):
        positive_timeout(self.timeout)
        if self.host_type not in HOST_TYPES:
            raise InvalidRequest("host_type must be auto, ws, or fusion.")
        if self.guest_os not in GUEST_SYSTEMS:
            raise InvalidRequest("guest_os must be windows, linux, macos, freebsd, or posix.")
        if self.guest_root is None:
            if not self.windows_guest:
                raise InvalidRequest(
                    "Set an absolute guest_root in a private directory for POSIX guests; "
                    "use --guest-os linux init for an example."
                )
            object.__setattr__(self, "guest_root", "C:\\GuestBridge")
        validate_guest_root(self.guest_root, windows=self.windows_guest)
        validate_posix_path(self.guest_shell)
        Workspace(self.host_root)
        try:
            address = ipaddress.ip_address(self.console_host)
        except ValueError:
            raise InvalidRequest("Console host must be a numeric loopback address.") from None
        if not address.is_loopback:
            raise InvalidRequest("Only loopback console connections are supported.")
        if type(self.console_port) is not int or not 1 <= self.console_port <= 65535:
            raise InvalidRequest("Console port must be an integer from 1 to 65535.")
        if type(self.allow_unauthenticated) is not bool:
            raise InvalidRequest("allow_unauthenticated must be a boolean.")
        for field in ("username", "password_env", "encoding"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
            ):
                raise InvalidRequest(f"{field} must be a nonempty single-line string.")
        if self.password_env and ("=" in self.password_env or len(self.password_env) > 128):
            raise InvalidRequest("Invalid password environment variable name.")
        if self.encoding:
            try:
                codecs.lookup(self.encoding)
            except LookupError:
                raise InvalidRequest("Unknown vmrun text encoding.") from None

    @property
    def windows_guest(self):
        return self.guest_os == "windows"

    @property
    def vmrun_type(self):
        if self.host_type != "auto":
            return self.host_type
        return "fusion" if sys.platform == "darwin" else "ws"


def load_config(filename=None, **overrides):
    settings = {}
    base = Path.cwd()
    if filename is not None:
        path = Path(filename).resolve()
        base = path.parent
        try:
            with path.open("rb") as stream:
                settings = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise InvalidRequest(f"Cannot read config: {exc}") from None
    console = settings.pop("console", {})
    if not isinstance(console, dict):
        raise InvalidRequest("[console] must be a TOML table.")
    if set(console) - {"host", "port", "allow_unauthenticated"}:
        raise InvalidRequest("Unknown console setting; see vmb init for the supported fields.")
    if "host" in console:
        settings["console_host"] = console["host"]
    if "port" in console:
        settings["console_port"] = console["port"]
    if "allow_unauthenticated" in console:
        settings["allow_unauthenticated"] = console["allow_unauthenticated"]
    if set(settings) - Config.__dataclass_fields__.keys():
        raise InvalidRequest("Unknown config setting. Password values are not accepted in config.")
    for field in ("vmx", "vmrun", "host_root"):
        if field in settings:
            if not isinstance(settings[field], str) or not settings[field]:
                raise InvalidRequest(f"{field} must be a path string.")
            settings[field] = (base / settings[field]).resolve()
    for field, value in overrides.items():
        if value is not None:
            settings[field] = (
                Path(value).resolve() if field in ("vmx", "vmrun", "host_root") else value
            )
    if "host_root" not in settings:
        settings["host_root"] = base / ".guest-bridge"
    return Config(**settings)


def find_vmrun(explicit=None):
    if explicit is not None:
        path = Path(explicit).resolve()
    else:
        if sys.platform == "win32":
            locations = [
                Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
                / "VMware/VMware Workstation/vmrun.exe"
            ]
        elif sys.platform == "darwin":
            locations = [Path("/Applications/VMware Fusion.app/Contents/Library/vmrun")]
        else:
            locations = [Path("/usr/bin/vmrun"), Path("/usr/lib/vmware/bin/vmrun")]
        candidate = shutil.which("vmrun")
        if candidate:
            locations.append(Path(candidate))
        path = next((entry for entry in locations if entry.is_file()), None)
    if path is None or not path.is_file():
        raise InvalidRequest(
            "vmrun not found. Install VMware Workstation (Windows/Linux) or Fusion (macOS), "
            "or specify --vmrun."
        )
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise InvalidRequest("vmrun is not executable; check its permissions or specify --vmrun.")
    return path
