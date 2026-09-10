import os
import re
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import InvalidRequest

_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def relative_parts(value, *, windows=True):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise InvalidRequest(
            "File paths must be nonempty relative strings, at most 4096 characters."
        )
    path = PureWindowsPath(value) if windows else PurePosixPath(value)
    if path.drive or path.root:
        raise InvalidRequest("File paths must be relative to the configured root.")
    if not windows and "\\" in value:
        raise InvalidRequest("POSIX paths must use forward slashes, not backslashes.")
    parts = (value.replace("\\", "/") if windows else value).split("/")
    for part in parts:
        if (
            part in ("", ".", "..")
            or any(ord(c) < 32 or ord(c) == 127 for c in part)
            or windows
            and (
                part.rstrip(" .") != part
                or any(c in '<>:"|?*' for c in part)
                or _RESERVED.match(part)
            )
        ):
            raise InvalidRequest("Path contains traversal, a device name, or an invalid component.")
    if parts[0].casefold() == ".vmb":
        raise InvalidRequest("The .vmb directory is reserved for script requests.")
    return parts


def validate_posix_path(value):
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise InvalidRequest("A POSIX guest path must be absolute, for example /home/user/bridge.")
    relative_parts(value[1:].rstrip("/"), windows=False)
    return PurePosixPath(value)


def validate_guest_root(value, *, windows=True):
    if not windows:
        return validate_posix_path(value)
    if not isinstance(value, str):
        raise InvalidRequest("guest_root must be a Windows directory path.")
    path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or len(path.drive) != 2
        or not path.drive[0].isascii()
        or not path.drive[0].isalpha()
        or len(path.parts) < 2
    ):
        raise InvalidRequest(
            "guest_root must be a directory such as C:\\GuestBridge, not a drive root."
        )
    relative_parts(str(path.relative_to(path.anchor)))
    return path


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if self.root == Path(self.root.anchor):
            raise InvalidRequest("host_root must be a dedicated directory, not a drive root.")

    def resolve(self, value, *, internal=False):
        if internal:
            path = self.root.joinpath(*PureWindowsPath(value).parts)
        else:
            path = self.root.joinpath(*relative_parts(value, windows=os.name == "nt"))
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise InvalidRequest("Host path leaves host_root, possibly through a symbolic link.")
        return resolved

    def source(self, value):
        path = self.resolve(value)
        if not path.is_file():
            raise InvalidRequest("Source file does not exist under host_root.")
        return path

    def target(self, value, *, overwrite=False):
        path = self.resolve(value)
        if path.exists() and (not overwrite or not path.is_file()):
            raise InvalidRequest("Target already exists; choose another name or use --overwrite.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def publish_file(staged, target, *, overwrite=False):
    if overwrite:
        os.replace(staged, target)
        return
    # Exclusive creation also protects against a file appearing during a guest transfer.
    with staged.open("rb") as source, target.open("xb") as destination:
        try:
            shutil.copyfileobj(source, destination)
        except BaseException:
            destination.close()
            target.unlink(missing_ok=True)
            raise
