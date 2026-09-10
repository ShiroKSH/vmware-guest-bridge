import re
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


class Guest:
    def __init__(self, config, runner):
        self.config = config
        self.runner = runner
        self.workspace = Workspace(config.host_root)
        self.root = validate_guest_root(config.guest_root)

    def _path(self, relative):
        return str(self.root.joinpath(*relative_parts(relative)))

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
        remote = self.root.joinpath(*relative_parts(path))
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

    def powershell_file(self, file, *, interactive=False):
        path = self.workspace.source(file)
        if path.stat().st_size > MAX_SCRIPT_BYTES:
            raise InvalidRequest("PowerShell script exceeds 256 KiB.")
        try:
            script = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            raise InvalidRequest("PowerShell scripts must use UTF-8 encoding.") from None
        return self.powershell(script, interactive=interactive)

    def powershell(self, script, *, interactive=False):
        if not isinstance(script, str) or not script.strip() or "\x00" in script:
            raise InvalidRequest(
                "script must be nonempty PowerShell source without NUL characters."
            )
        if len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise InvalidRequest("PowerShell script exceeds 256 KiB.")
        if type(interactive) is not bool:
            raise InvalidRequest("interactive must be a boolean.")
        request_id = uuid.uuid4().hex
        local_script = self.workspace.resolve(f".vmb/{request_id}.ps1", internal=True)
        local_script.parent.mkdir(parents=True, exist_ok=True)
        remote_script = str(self.root / ".vmb" / f"{request_id}.ps1")
        remote_result = str(self.root / ".vmb" / f"{request_id}.json")
        local_script.write_text(wrap_script(script, remote_result), encoding="utf-8-sig")
        deadline = time.monotonic() + self.config.timeout

        def call(command, *args, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining < 0.1:
                raise OutcomeUnknown("Request deadline reached; inspect before retrying.")
            return self.runner.run(command, *args, timeout=remaining, **kwargs)

        try:
            self._ensure_directory(self.root / ".vmb", call)
            call("CopyFileFromHostToGuest", local_script, remote_script)
            flags = ["-noWait"]
            if interactive:
                flags.append("-interactive")
            call(
                "runProgramInGuest",
                *flags,
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                remote_script,
            )
            while deadline - time.monotonic() >= 0.1:
                try:
                    return self.result(request_id, timeout=min(5, deadline - time.monotonic()))
                except (BridgeError, OSError) as exc:
                    if isinstance(exc, InvalidRequest):
                        raise
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            raise OutcomeUnknown(
                "No complete PowerShell result before the deadline. "
                "Use result with this request_id; do not resubmit the script."
            )
        except OutcomeUnknown as exc:
            exc.details.update(request_id=request_id, guest_result=remote_result)
            raise

    def result(self, request_id, *, timeout=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise InvalidRequest(
                "request_id must be the 32 lowercase hex characters returned by ps."
            )
        remote = str(self.root / ".vmb" / f"{request_id}.json")
        local = self.workspace.resolve(f".vmb/{request_id}.json", internal=True)
        local.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".result-", dir=local.parent) as staging:
            partial = Path(staging) / "result.json"
            self.runner.run("CopyFileFromGuestToHost", remote, partial, timeout=timeout)
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
