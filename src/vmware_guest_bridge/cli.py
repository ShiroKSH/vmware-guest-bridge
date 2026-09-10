import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import EXAMPLE, load_config
from .errors import BridgeError, InvalidRequest
from .protocol import Bridge, emit, failed_guest, serve
from .vmrun import read_secret


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise InvalidRequest(message + "; use --help for usage.")


def parser():
    root = Parser(
        prog="vmb",
        description="Windows guest commands and local VMware console access.",
        epilog="Examples: vmb init | vmb --config bridge.toml status | "
        "vmb --config bridge.toml ps --file hello.ps1",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    root.add_argument(
        "--config", metavar="FILE", help="explicit TOML config; no config is auto-loaded"
    )
    root.add_argument("--json", action="store_true", help="one JSON envelope on stdout")
    root.add_argument(
        "--no-input", action="store_true", help="never prompt for credentials or text"
    )
    for name, help_text in (
        ("vmx", "target .vmx file"),
        ("vmrun", "path to the installed vmrun executable"),
        ("username", "guest Windows username"),
        ("password-env", "name of an already exported guest-password environment variable"),
        ("host-root", "directory containing transfer files and request records"),
        ("guest-root", "Windows guest directory for transfers and request records"),
        ("encoding", "encoding of vmrun diagnostic output"),
        ("console-host", "numeric loopback address (default: 127.0.0.1)"),
    ):
        root.add_argument("--" + name, help=help_text)
    root.add_argument("--timeout", type=float, help="operation timeout in seconds (default: 60)")
    root.add_argument("--console-port", type=int, help="local RFB port (default: 5900)")
    root.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        default=None,
        help="opt in to unauthenticated loopback RFB; see docs/console.md",
    )
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write an example config without overwriting files")
    init.add_argument(
        "--output", default="bridge.toml", help="new config path (default: bridge.toml)"
    )
    commands.add_parser(
        "doctor", help="check local config and executable paths without running a VM"
    )
    commands.add_parser("status", help="read VMware Tools state; no guest password needed")
    commands.add_parser("processes", help="list guest processes using VMware Tools")
    mkdir = commands.add_parser("mkdir", help="create a directory below guest_root")
    mkdir.add_argument("--path", required=True)
    for name in ("upload", "download"):
        transfer = commands.add_parser(name, help=f"{name} one file between the configured roots")
        transfer.add_argument("--source", required=True)
        transfer.add_argument("--target", required=True)
        transfer.add_argument("--overwrite", action="store_true")
    ps = commands.add_parser("ps", help="run a UTF-8 PowerShell file from host_root")
    ps.add_argument("--file", required=True)
    ps.add_argument(
        "--interactive", action="store_true", help="use the logged-in guest desktop session"
    )
    result = commands.add_parser(
        "result", help="collect a previous PowerShell result without rerunning"
    )
    result.add_argument("request_id")
    commands.add_parser(
        "serve", help="read JSON lines from stdin; credentials must be noninteractive"
    )
    console = commands.add_parser("console", help="screen and input through the local RFB console")
    actions = console.add_subparsers(dest="console_command", required=True)
    actions.add_parser("probe", help="read console dimensions and desktop name")
    screenshot = actions.add_parser("screenshot", help="save a complete PNG below host_root")
    screenshot.add_argument("--target", required=True)
    screenshot.add_argument("--overwrite", action="store_true")
    key = actions.add_parser("key", help="send a key or chord, for example Ctrl+Alt+Delete")
    key.add_argument("key")
    text = actions.add_parser(
        "type", help="type ASCII using the guest's US English keyboard layout"
    )
    inputs = text.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text", help="ordinary text; use --secret for sensitive input")
    inputs.add_argument(
        "--secret", action="store_true", help="read text with a hidden terminal prompt"
    )
    click = actions.add_parser("click", help="click guest framebuffer coordinates")
    click.add_argument("--x", type=int, required=True)
    click.add_argument("--y", type=int, required=True)
    click.add_argument("--button", choices=("left", "middle", "right"), default="left")
    return root


def _request(args):
    command = args.command
    if command == "console":
        command += "." + args.console_command
    request = {"action": command}
    for name in (
        "path",
        "source",
        "target",
        "overwrite",
        "file",
        "interactive",
        "request_id",
        "key",
        "text",
        "x",
        "y",
        "button",
    ):
        if hasattr(args, name):
            request[name] = getattr(args, name)
    if command == "console.type" and args.secret:
        request["text"] = read_secret("Guest input: ", no_input=args.no_input)
    return request


def _human(value):
    for key, item in value.items():
        if isinstance(item, dict | list):
            print(f"{key}: {json.dumps(item, ensure_ascii=True)}")
        else:
            # Console names and guest diagnostics are untrusted terminal input.
            safe = "".join(c if c in "\n\t" or c.isprintable() else "?" for c in str(item))
            print(f"{key}: {safe}")


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    bridge = None
    try:
        args = parser().parse_args(arguments)
        if args.command == "init":
            with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(EXAMPLE)
            result = {"path": str(Path(args.output).resolve())}
        else:
            overrides = {
                name: getattr(args, name)
                for name in (
                    "vmx",
                    "vmrun",
                    "username",
                    "password_env",
                    "host_root",
                    "guest_root",
                    "timeout",
                    "encoding",
                    "console_host",
                    "console_port",
                    "allow_unauthenticated",
                )
            }
            config = load_config(args.config, **overrides)
            bridge = Bridge(config, no_input=args.no_input or args.command == "serve")
            if args.command == "serve":
                return serve(bridge, getattr(sys.stdin, "buffer", sys.stdin), sys.stdout)
            result = bridge.runner.redact(bridge.dispatch(_request(args)))
        if json_output:
            emit(sys.stdout, {"ok": not failed_guest(result), "result": result})
        else:
            _human(result)
        return 1 if failed_guest(result) else 0
    except (BridgeError, OSError, UnicodeError) as exc:
        error = (
            exc if isinstance(exc, BridgeError) else BridgeError(f"File or encoding error: {exc}")
        )
        envelope = {"ok": False, "error": error.as_dict()}
        if bridge:
            envelope = bridge.runner.redact(envelope)
        if json_output:
            emit(sys.stdout, envelope)
        else:
            message = envelope["error"]["message"]
            safe = "".join(c if c.isprintable() else " " for c in message)
            print(f"vmb: {safe}", file=sys.stderr)
            if "request_id" in envelope["error"]:
                print(f"request_id: {envelope['error']['request_id']}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print(
            "Interrupted. A guest action may still be running; inspect before retrying.",
            file=sys.stderr,
        )
        return 130
    finally:
        if bridge:
            bridge.close()
