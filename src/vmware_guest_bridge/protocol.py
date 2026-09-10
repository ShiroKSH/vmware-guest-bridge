import json

from .config import find_vmrun
from .console import Console
from .errors import BridgeError, InvalidRequest
from .guest import Guest
from .jsonio import loads
from .vmrun import Vmrun

MAX_LINE = 1024 * 1024
FIELDS = {
    "doctor": (set(), set()),
    "status": (set(), set()),
    "processes": (set(), set()),
    "mkdir": ({"path"}, set()),
    "upload": ({"source", "target"}, {"overwrite"}),
    "download": ({"source", "target"}, {"overwrite"}),
    "ps": (set(), {"script", "file", "interactive"}),
    "sh": (set(), {"script", "file"}),
    "result": ({"request_id"}, set()),
    "console.probe": (set(), set()),
    "console.screenshot": ({"target"}, {"overwrite"}),
    "console.key": ({"key"}, set()),
    "console.type": ({"text"}, set()),
    "console.click": ({"x", "y"}, {"button"}),
    "quit": (set(), set()),
}


def validate_request(request):
    if not isinstance(request, dict):
        raise InvalidRequest("Request must be a JSON object.")
    request_id = request.get("id")
    if request_id is not None and (
        type(request_id) not in (str, int)
        or isinstance(request_id, str)
        and len(request_id) > 128
        or isinstance(request_id, int)
        and abs(request_id) > 2**53 - 1
    ):
        raise InvalidRequest("id must be null, a string up to 128 characters, or a safe integer.")
    action = request.get("action")
    if not isinstance(action, str) or action not in FIELDS:
        raise InvalidRequest("Unknown action; see docs/protocol.md.")
    required, optional = FIELDS[action]
    if required - request.keys() or request.keys() - required - optional - {"id", "action"}:
        raise InvalidRequest("Missing or unknown request fields; see docs/protocol.md.")
    for name in ("overwrite", "interactive"):
        if name in request and type(request[name]) is not bool:
            raise InvalidRequest(f"{name} must be a boolean.")
    if action in ("ps", "sh") and ("script" in request) == ("file" in request):
        raise InvalidRequest(f"{action} requires exactly one of script or file.")
    return action


class Bridge:
    def __init__(self, config, *, no_input=False):
        self.config = config
        self.runner = Vmrun(config, no_input=no_input)
        self.guest = Guest(config, self.runner)
        self.console = Console(config)

    def close(self):
        self.console.close()
        self.runner.close()

    def dispatch(self, request):
        action = validate_request(request)
        if action == "doctor":
            executable = find_vmrun(self.config.vmrun)
            vmx = self.config.vmx
            if vmx is None or not vmx.is_file() or vmx.suffix.lower() != ".vmx":
                raise InvalidRequest("Select an existing .vmx file with --vmx or in your config.")
            return {
                "vmrun": str(executable),
                "vmx": str(vmx),
                "host_type": self.config.vmrun_type,
                "guest_os": self.config.guest_os,
                "guest_shell": "powershell.exe"
                if self.config.windows_guest
                else self.config.guest_shell,
                "host_root": str(self.guest.workspace.root),
                "guest_root": str(self.guest.root),
                "console_enabled": self.config.allow_unauthenticated,
                "checks": "Local configuration and file existence; no guest operation performed.",
            }
        if action == "status":
            return self.guest.status()
        if action == "processes":
            return self.guest.processes()
        if action == "mkdir":
            return self.guest.mkdir(request["path"])
        if action in ("upload", "download"):
            return getattr(self.guest, action)(
                request["source"], request["target"], overwrite=request.get("overwrite", False)
            )
        if action == "ps":
            if "file" in request:
                return self.guest.powershell_file(
                    request["file"], interactive=request.get("interactive", False)
                )
            return self.guest.powershell(
                request["script"], interactive=request.get("interactive", False)
            )
        if action == "result":
            return self.guest.result(request["request_id"])
        if action == "sh":
            if "file" in request:
                return self.guest.shell_file(request["file"])
            return self.guest.shell(request["script"])
        if action == "console.probe":
            return self.console.probe()
        if action == "console.screenshot":
            return self.console.screenshot(
                request["target"], overwrite=request.get("overwrite", False)
            )
        if action == "console.key":
            return self.console.press(request["key"])
        if action == "console.type":
            return self.console.type_text(request["text"])
        if action == "console.click":
            return self.console.click(
                request["x"], request["y"], button=request.get("button", "left")
            )
        if action == "quit":
            return {"closed": True}
        raise AssertionError("Validated action has no implementation.")


def emit(stream, value):
    stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
    stream.flush()


def failed_guest(result):
    return isinstance(result, dict) and result.get("guest", {}).get("ok") is False


def serve(bridge, source, destination):
    emit(destination, {"event": "ready", "protocol": 1})
    while True:
        line = source.readline(MAX_LINE + 1)
        if not line:
            return 0
        if len(line) > MAX_LINE or isinstance(line, str) and len(line.encode("utf-8")) > MAX_LINE:
            emit(
                destination,
                {
                    "id": None,
                    "ok": False,
                    "error": InvalidRequest("Request line exceeds 1 MiB.").as_dict(),
                },
            )
            return 2
        if not line.strip():
            continue
        request_id = None
        try:
            request = loads(line.decode("utf-8") if isinstance(line, bytes) else line)
            action = validate_request(request)
            request_id = request.get("id")
            result = bridge.dispatch(request)
            emit(
                destination,
                bridge.runner.redact(
                    {
                        "id": request_id,
                        "ok": not failed_guest(result),
                        "result": result,
                    }
                ),
            )
            if action == "quit":
                return 0
        except (ValueError, RecursionError):
            emit(
                destination,
                {
                    "id": request_id,
                    "ok": False,
                    "error": InvalidRequest("Invalid JSON request.").as_dict(),
                },
            )
        except BridgeError as exc:
            emit(
                destination,
                bridge.runner.redact(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": exc.as_dict(),
                    }
                ),
            )
        except OSError as exc:
            emit(
                destination,
                bridge.runner.redact(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": BridgeError(f"File or transport error: {exc}").as_dict(),
                    }
                ),
            )
