import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path

from .errors import BridgeError, InvalidRequest, OutcomeUnknown
from .jsonio import loads
from .paths import Workspace, publish_file, relative_parts, validate_guest_root

MAX_SCRIPT_BYTES = 256 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def wrap_script(script, remote_result):
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$global:LASTEXITCODE = 0\n"
        "try {\n"
        "  $bridgeOutput = @(& {\n" + script + "\n  })\n"
        '  if ($LASTEXITCODE -ne 0) { throw "Native command exited with $LASTEXITCODE" }\n'
        "  $bridgeResult = @{ok=$true; data=$bridgeOutput}\n"
        "} catch {\n"
        "  $bridgeResult = @{ok=$false; error=$_.Exception.Message}\n"
        "}\n"
        "$bridgeJson = $bridgeResult | ConvertTo-Json -Depth 16 -Compress\n"
        f"if ([Text.Encoding]::UTF8.GetByteCount($bridgeJson) -gt {MAX_RESULT_BYTES}) {{\n"
        '  $bridgeJson = \'{"ok":false,"error":"Result exceeds 8 MiB"}\'\n'
        "}\n"
        f"$bridgeTarget = {ps_quote(remote_result)}\n"
        "[IO.File]::WriteAllText($bridgeTarget + '.tmp', $bridgeJson, "
        "[Text.UTF8Encoding]::new($false))\n"
        "[IO.File]::Move($bridgeTarget + '.tmp', $bridgeTarget)\n"
    )


def wrap_shell(remote_script, remote_base, guest_root, shell):
    quote = shlex.quote
    stdout = str(remote_base) + ".stdout"
    stderr = str(remote_base) + ".stderr"
    status = str(remote_base) + ".exitcode"
    # A separate shell preserves explicit exit and syntax-error statuses from the user's script.
    return (
        "#!/bin/sh\n"
        "umask 077\n"
        f"cd {quote(str(guest_root))} || exit 125\n"
        f"{quote(shell)} {quote(str(remote_script))} "
        f">{quote(stdout + '.tmp')} 2>{quote(stderr + '.tmp')} </dev/null\n"
        "bridge_status=$?\n"
        f"mv -f {quote(stdout + '.tmp')} {quote(stdout)} &&\n"
        f"mv -f {quote(stderr + '.tmp')} {quote(stderr)} &&\n"
        f"printf '%s\\n' \"$bridge_status\" >{quote(status + '.tmp')} &&\n"
        f"mv -f {quote(status + '.tmp')} {quote(status)}\n"
    )


class Guest:
    def __init__(self, config, runner):
        self.config = config
        self.runner = runner
        self.workspace = Workspace(config.host_root)
        self.root = validate_guest_root(config.guest_root, windows=config.windows_guest)

    def _path(self, relative):
        return str(self.root.joinpath(*relative_parts(relative, windows=self.config.windows_guest)))

    def _ensure_directory(self, path, call):
        for directory in reversed((path, *path.parents)):
            if directory == directory.parent:
                continue
            code, _ = call("directoryExistsInGuest", str(directory), check=False)
            if code:
                call("createDirectoryInGuest", str(directory))

    def status(self):
        _, tools_state = self.runner.run("checkToolsState", authenticated=False)
        return {"vmx": str(self.config.vmx), "tools_state": tools_state}

    def processes(self):
        _, processes = self.runner.run("listProcessesInGuest")
        return {"processes": processes}

    def mkdir(self, path):
        remote = self.root.joinpath(*relative_parts(path, windows=self.config.windows_guest))
        self._ensure_directory(remote, self.runner.run)
        return {"guest_path": str(remote)}

    def upload(self, source, target, *, overwrite=False):
        local = self.workspace.source(source)
        remote = self._path(target)
        self._ensure_directory(self.root, self.runner.run)
        code, _ = self.runner.run("fileExistsInGuest", remote, check=False)
        if code == 0 and not overwrite:
            raise InvalidRequest("Guest target exists; choose another name or use --overwrite.")
        self.runner.run("CopyFileFromHostToGuest", local, remote)
        return {"guest_path": remote, "bytes": local.stat().st_size}

    def download(self, source, target, *, overwrite=False):
        remote = self._path(source)
        local = self.workspace.target(target, overwrite=overwrite)
        with tempfile.TemporaryDirectory(prefix=".transfer-", dir=local.parent) as staging:
            partial = Path(staging) / "download"
            self.runner.run("CopyFileFromGuestToHost", remote, partial)
            if not partial.is_file():
                raise BridgeError("vmrun reported success but did not create the downloaded file.")
            # Revalidate after vmrun: the guest cannot choose the destination path.
            local = self.workspace.target(target, overwrite=overwrite)
            publish_file(partial, local, overwrite=overwrite)
        return {"path": str(local), "bytes": local.stat().st_size}

    def _read_script(self, file):
        path = self.workspace.source(file)
        if path.stat().st_size > MAX_SCRIPT_BYTES:
            raise InvalidRequest("Script exceeds 256 KiB.")
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            raise InvalidRequest("Scripts must use UTF-8 encoding.") from None

    def _validate_script(self, script):
        if not isinstance(script, str) or not script.strip() or "\x00" in script:
            raise InvalidRequest("script must be nonempty source without NUL characters.")
        if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise InvalidRequest("Script exceeds 256 KiB.")

    def _store_script(self, name, content, *, encoding="utf-8"):
        local = self.workspace.resolve(f".vmb/{name}", internal=True)
        local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        local.write_text(content, encoding=encoding, newline="\n")
        return local, str(self.root / ".vmb" / name)

    def powershell_file(self, file, *, interactive=False):
        return self.powershell(self._read_script(file), interactive=interactive)

    def powershell(self, script, *, interactive=False):
        if not self.config.windows_guest:
            raise InvalidRequest("ps requires guest_os=windows; use sh for POSIX guests.")
        self._validate_script(script)
        if type(interactive) is not bool:
            raise InvalidRequest("interactive must be a boolean.")
        request_id = uuid.uuid4().hex
        remote_result = str(self.root / ".vmb" / f"{request_id}.json")
        script_file = self._store_script(
            f"{request_id}.ps1", wrap_script(script, remote_result), encoding="utf-8-sig"
        )
        flags = ["-noWait"]
        if interactive:
            flags.append("-interactive")
        return self._execute(
            request_id,
            [script_file],
            [
                *flags,
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_file[1],
            ],
            remote_result,
        )

    def shell_file(self, file):
        return self.shell(self._read_script(file))

    def shell(self, script):
        if self.config.windows_guest:
            raise InvalidRequest("sh requires a POSIX guest_os; use ps for Windows guests.")
        self._validate_script(script)
        request_id = uuid.uuid4().hex
        remote_base = self.root / ".vmb" / request_id
        script_file = self._store_script(f"{request_id}.sh", script.replace("\r\n", "\n"))
        wrapper = self._store_script(
            f"{request_id}.runner.sh",
            wrap_shell(script_file[1], remote_base, self.root, self.config.guest_shell),
        )
        return self._execute(
            request_id,
            [script_file, wrapper],
            ["-noWait", "/bin/sh", wrapper[1]],
            str(remote_base) + ".exitcode",
        )

    def _call_until(self, deadline):
        def call(command, *args, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining < 0.1:
                raise OutcomeUnknown("Request deadline reached; inspect before retrying.")
            return self.runner.run(command, *args, timeout=remaining, **kwargs)

        return call

    def _execute(self, request_id, files, launch, remote_result):
        deadline = time.monotonic() + self.config.timeout
        call = self._call_until(deadline)
        try:
            self._ensure_directory(self.root / ".vmb", call)
            for local, remote in files:
                call("CopyFileFromHostToGuest", local, remote)
            call("runProgramInGuest", *launch)
            while deadline - time.monotonic() >= 0.1:
                try:
                    return self.result(request_id, timeout=min(5, deadline - time.monotonic()))
                except (BridgeError, OSError) as exc:
                    if isinstance(exc, InvalidRequest):
                        raise
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            raise OutcomeUnknown(
                "No complete script result before the deadline. "
                "Use result with this request_id; do not resubmit the script."
            )
        except OutcomeUnknown as exc:
            exc.details.update(request_id=request_id, guest_result=remote_result)
            raise

    def _shell_result(self, request_id, call):
        local = self.workspace.resolve(f".vmb/{request_id}.exitcode", internal=True)
        local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        remote_base = str(self.root / ".vmb" / request_id)
        with tempfile.TemporaryDirectory(prefix=".result-", dir=local.parent) as staging:
            status = Path(staging) / "exitcode"
            call("CopyFileFromGuestToHost", remote_base + ".exitcode", status)
            if not status.is_file() or status.stat().st_size > 4:
                raise BridgeError("Missing or invalid shell exit code.")
            raw_status = status.read_bytes()
            if not re.fullmatch(rb"(?:0|[1-9][0-9]{0,2})\n", raw_status):
                raise BridgeError("Guest shell exit code must be an integer from 0 to 255.")
            exit_code = int(raw_status)
            if exit_code > 255:
                raise BridgeError("Guest shell exit code must be an integer from 0 to 255.")
            result = {"ok": exit_code == 0, "exit_code": exit_code}
            remaining = MAX_RESULT_BYTES
            for name in ("stdout", "stderr"):
                output = Path(staging) / name
                call("CopyFileFromGuestToHost", remote_base + "." + name, output)
                if not output.is_file() or output.stat().st_size > remaining:
                    raise BridgeError("Missing shell output or combined output exceeds 8 MiB.")
                raw = output.read_bytes()
                remaining -= len(raw)
                result[name] = raw.decode("utf-8", errors="replace")
            for name in ("stdout", "stderr"):
                target = self.workspace.resolve(f".vmb/{request_id}.{name}", internal=True)
                publish_file(Path(staging) / name, target, overwrite=True)
            publish_file(status, local, overwrite=True)
        return {"request_id": request_id, "guest": result}

    def result(self, request_id, *, timeout=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise InvalidRequest(
                "request_id must be the 32 lowercase hex characters returned by ps or sh."
            )
        call = self._call_until(
            time.monotonic() + (self.config.timeout if timeout is None else timeout)
        )
        if not self.config.windows_guest:
            return self._shell_result(request_id, call)
        remote = str(self.root / ".vmb" / f"{request_id}.json")
        local = self.workspace.resolve(f".vmb/{request_id}.json", internal=True)
        local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".result-", dir=local.parent) as staging:
            partial = Path(staging) / "result.json"
            call("CopyFileFromGuestToHost", remote, partial)
            if not partial.is_file() or partial.stat().st_size > MAX_RESULT_BYTES:
                raise BridgeError("Missing or oversized guest result.")
            try:
                result = loads(partial.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError, RecursionError):
                raise BridgeError("Guest result is not valid UTF-8 JSON.") from None
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise BridgeError("Guest result has an invalid envelope.")
            publish_file(partial, local, overwrite=True)
        return {"request_id": request_id, "guest": result}
