import getpass
import locale
import os
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass, field

from .config import find_vmrun, positive_timeout
from .errors import BridgeError, InvalidRequest, OutcomeUnknown


@dataclass
class Credentials:
    username: str
    password: str = field(repr=False)


class Vmrun:
    def __init__(self, config, *, no_input=False):
        self.config = config
        self.no_input = no_input
        self._credentials = None

    def redact(self, value):
        if isinstance(value, dict):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str) and self._credentials and self._credentials.password:
            return value.replace(self._credentials.password, "[redacted]")
        return value

    def credentials(self):
        if self._credentials is None:
            if not self.config.username:
                raise InvalidRequest("Set the guest username with --username or in your config.")
            name = self.config.password_env
            if name:
                if name not in os.environ:
                    raise InvalidRequest("The configured password environment variable is not set.")
                password = os.environ[name]
            else:
                password = read_secret("Guest password: ", no_input=self.no_input)
            if "\x00" in password:
                raise InvalidRequest("Guest password contains a NUL character.")
            self._credentials = Credentials(self.config.username, password)
        return self._credentials

    def close(self):
        self._credentials = None

    def run(self, command, *arguments, authenticated=True, timeout=None, check=True):
        vmx = self.config.vmx
        if vmx is None or vmx.suffix.lower() != ".vmx" or not vmx.is_file():
            raise InvalidRequest("Select an existing .vmx file with --vmx or in your config.")
        executable = find_vmrun(self.config.vmrun)
        seconds = positive_timeout(self.config.timeout if timeout is None else timeout)
        args = [str(executable), "-T", self.config.vmrun_type]
        if authenticated:
            credentials = self.credentials()
            args += ["-gu", credentials.username, "-gp", credentials.password]
        args += [command, str(vmx), *map(str, arguments)]
        child_env = os.environ.copy()
        if self.config.password_env:
            child_env = {
                name: value
                for name, value in child_env.items()
                if (name.casefold() if os.name == "nt" else name)
                != (
                    self.config.password_env.casefold()
                    if os.name == "nt"
                    else self.config.password_env
                )
            }
        try:
            with tempfile.TemporaryFile() as output:
                with subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=child_env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ) as process:
                    try:
                        returncode = process.wait(timeout=seconds)
                    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                        process.kill()
                        process.wait()
                        if isinstance(exc, KeyboardInterrupt):
                            raise
                        raise OutcomeUnknown(
                            f"{command} timed out. The guest operation may still be running; "
                            "inspect its state before retrying."
                        ) from None
                output.seek(0)
                raw = output.read(1024 * 1024 + 1)
        except OSError as exc:
            raise BridgeError(self.redact(f"Cannot run vmrun: {exc}")) from None
        encoding = self.config.encoding or locale.getpreferredencoding(False)
        message = self.redact(raw[: 1024 * 1024].decode(encoding, errors="replace").strip())
        if len(raw) > 1024 * 1024:
            message += "\n[vmrun output truncated at 1 MiB]"
        if check and returncode:
            raise BridgeError(
                f"{command} failed: {message or 'no diagnostic output'}", returncode=returncode
            )
        return returncode, message


def read_secret(prompt, *, no_input=False):
    if no_input or not sys.stdin.isatty():
        raise InvalidRequest(
            "Hidden input requires a terminal. For guest authentication, use --password-env "
            "with the name of an already exported variable."
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt, stream=sys.stderr)
    except (getpass.GetPassWarning, EOFError):
        raise InvalidRequest("Cannot read a password without echoing it.") from None
