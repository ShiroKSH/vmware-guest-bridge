import contextlib
import socket
import struct
import tempfile
import time
import zlib
from pathlib import Path

from .errors import BridgeError, InvalidRequest, OutcomeUnknown
from .paths import Workspace, publish_file

MAX_PIXELS = 16_777_216
MAX_RECTANGLES = 4096
KEYS = {
    "ctrl": 0xFFE3,
    "alt": 0xFFE9,
    "shift": 0xFFE1,
    "win": 0xFFEB,
    "enter": 0xFF0D,
    "tab": 0xFF09,
    "escape": 0xFF1B,
    "space": 0x20,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "insert": 0xFF63,
    "up": 0xFF52,
    "down": 0xFF54,
    "left": 0xFF51,
    "right": 0xFF53,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "menu": 0xFF67,
    "plus": 0x2B,
    **{f"f{n}": 0xFFBD + n for n in range(1, 13)},
}
SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./", strict=True))


def key_codes(chord):
    if not isinstance(chord, str) or len(chord) > 128:
        raise InvalidRequest("key must be a chord such as Ctrl+Alt+Delete.")
    names = chord.lower().split("+")
    if not 1 <= len(names) <= 8 or len(set(names)) != len(names):
        raise InvalidRequest("A chord must contain 1 to 8 distinct keys.")
    codes = []
    for name in names:
        if name in KEYS:
            codes.append(KEYS[name])
        elif len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
            codes.append(ord(name))
        else:
            raise InvalidRequest("Unknown key. Use a named key, letter, or digit; see docs/cli.md.")
    return codes


def png_bytes(width, height, pixels):
    rows = bytearray()
    for y in range(height):
        row = pixels[y * width * 4 : (y + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        rows.append(0)
        rows.extend(rgb)

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class Console:
    def __init__(self, config):
        self.config = config
        self.workspace = Workspace(config.host_root)
        self._socket = None
        self._deadline = 0
        self._input_started = False
        self._synchronized = False
        self.width = self.height = 0
        self.name = ""

    def close(self):
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._synchronized = False

    def _remaining(self):
        seconds = self._deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Console operation deadline reached.")
        return seconds

    def _receive(self, size):
        payload = bytearray()
        while len(payload) < size:
            self._socket.settimeout(self._remaining())
            part = self._socket.recv(min(128 * 1024, size - len(payload)))
            if not part:
                raise ConnectionError("Console closed the connection.")
            payload.extend(part)
        return payload

    def _send(self, payload):
        self._socket.settimeout(self._remaining())
        self._socket.sendall(payload)

    @contextlib.contextmanager
    def _session(self):
        self._deadline = time.monotonic() + self.config.timeout
        self._input_started = False
        try:
            if self._socket is None:
                self._connect()
            yield
        except (OSError, BridgeError) as exc:
            self.close()
            if self._input_started:
                raise OutcomeUnknown(
                    "Console failed during input. Some input may have reached the guest; "
                    "observe its screen before retrying."
                ) from None
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError(f"Console connection failed: {exc}") from None
        except BaseException:
            self.close()
            raise

    def _connect(self):
        if not self.config.allow_unauthenticated:
            raise InvalidRequest(
                "Console access is disabled. This client supports unauthenticated loopback RFB "
                "only; read docs/console.md before using --allow-unauthenticated."
            )
        self._socket = socket.create_connection(
            (self.config.console_host, self.config.console_port), timeout=self._remaining()
        )
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        banner = bytes(self._receive(12))
        if banner not in (b"RFB 003.003\n", b"RFB 003.007\n", b"RFB 003.008\n"):
            raise BridgeError("Unsupported RFB protocol; expected 3.3, 3.7, or 3.8.")
        self._send(banner)
        if banner == b"RFB 003.003\n":
            security = struct.unpack(">I", self._receive(4))[0]
            if security != 1:
                raise BridgeError("Console requires unsupported authentication or rejected access.")
        else:
            count = self._receive(1)[0]
            if count == 0:
                raise BridgeError("Console rejected the connection.")
            methods = self._receive(count)
            if 1 not in methods:
                raise BridgeError("Console requires authentication unsupported by this client.")
            self._send(b"\x01")
            # RFB 3.3/3.7 omit SecurityResult for the None security type.
            if banner == b"RFB 003.008\n" and self._receive(4) != b"\x00" * 4:
                raise BridgeError("Console rejected the connection.")
        self._send(b"\x01")  # Shared session: leave other viewers connected.
        header = self._receive(24)
        self.width, self.height = struct.unpack(">HH", header[:4])
        name_size = struct.unpack(">I", header[20:])[0]
        if (
            not self.width
            or not self.height
            or self.width > 8192
            or self.height > 8192
            or self.width * self.height > MAX_PIXELS
            or name_size > 4096
        ):
            raise BridgeError("Console dimensions or desktop name exceed supported limits.")
        self.name = self._receive(name_size).decode("utf-8", errors="replace")
        pixel_format = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        self._send(b"\x00\x00\x00\x00" + pixel_format)
        self._send(struct.pack(">BBHi", 2, 0, 1, 0))

    def probe(self):
        with self._session():
            return {"width": self.width, "height": self.height, "desktop_name": self.name}

    def _framebuffer(self):
        self._send(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
        count_pixels = self.width * self.height
        pixels = bytearray(count_pixels * 4)
        coverage = bytearray(count_pixels)
        covered = rectangles = transferred = 0
        for _ in range(1024):
            kind = self._receive(1)[0]
            if kind == 2:
                continue
            if kind == 3:
                length = struct.unpack(">I", self._receive(7)[3:])[0]
                if length > 1024 * 1024:
                    raise BridgeError("Console clipboard payload exceeds 1 MiB.")
                self._receive(length)
                continue
            if kind != 0:
                raise BridgeError("Unsupported framebuffer message.")
            count = struct.unpack(">H", self._receive(3)[1:])[0]
            rectangles += count
            if rectangles > MAX_RECTANGLES:
                raise BridgeError("Framebuffer update contains too many rectangles.")
            for _ in range(count):
                x, y, width, height, encoding = struct.unpack(">HHHHi", self._receive(12))
                if encoding != 0 or x + width > self.width or y + height > self.height:
                    raise BridgeError("Invalid framebuffer rectangle; reconnect after a resize.")
                transferred += width * height * 4
                if transferred > count_pixels * 16:
                    raise BridgeError("Framebuffer update exceeds the transfer limit.")
                rectangle = self._receive(width * height * 4)
                for row in range(height):
                    start = (y + row) * self.width + x
                    covered += coverage[start : start + width].count(0)
                    coverage[start : start + width] = b"\x01" * width
                    pixels[start * 4 : (start + width) * 4] = rectangle[
                        row * width * 4 : (row + 1) * width * 4
                    ]
            if covered == count_pixels:
                self._synchronized = True
                return pixels
        raise BridgeError("Console did not provide a complete framebuffer.")

    def screenshot(self, target, *, overwrite=False):
        if not isinstance(target, str) or Path(target).suffix.lower() != ".png":
            raise InvalidRequest("Screenshot target must end in .png.")
        local = self.workspace.target(target, overwrite=overwrite)
        # Workstation may not answer a second full-frame request on an idle connection.
        capture = Console(self.config)
        try:
            with capture._session():
                pixels = capture._framebuffer()
                width, height = capture.width, capture.height
                encoded = png_bytes(width, height, pixels)
        finally:
            capture.close()
        if self._socket is not None and (self.width, self.height) != (width, height):
            self.close()
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=local.parent) as staging:
            partial = Path(staging) / "screen.png"
            partial.write_bytes(encoded)
            local = self.workspace.target(target, overwrite=overwrite)
            publish_file(partial, local, overwrite=overwrite)
        return {"path": str(local), "width": width, "height": height}

    def _before_input(self):
        if not self._synchronized:
            self._framebuffer()

    def _key(self, code, down):
        self._input_started = True
        self._send(struct.pack(">BBHI", 4, int(down), 0, code))

    def _chord(self, codes):
        pressed = []
        try:
            for code in codes:
                pressed.append(code)
                self._key(code, True)
                time.sleep(min(0.04, self._remaining()))
        finally:
            release_error = None
            for code in reversed(pressed):
                try:
                    self._key(code, False)
                    time.sleep(min(0.01, self._remaining()))
                except OSError as exc:
                    release_error = exc
            if release_error is not None:
                raise release_error

    def press(self, key):
        codes = key_codes(key)
        with self._session():
            self._before_input()
            self._chord(codes)
        return {"sent": True}

    def type_text(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 1024:
            raise InvalidRequest("text must contain 1 to 1024 characters.")
        if any(c not in "\n\r\t" and not " " <= c <= "~" for c in text):
            raise InvalidRequest("Console typing supports printable ASCII, Enter, and Tab only.")
        with self._session():
            self._before_input()
            for char in text.replace("\r\n", "\n"):
                if char in "\n\r\t":
                    codes = [KEYS["tab" if char == "\t" else "enter"]]
                elif "A" <= char <= "Z" or char in SHIFTED:
                    codes = [KEYS["shift"], ord(SHIFTED.get(char, char.lower()))]
                else:
                    codes = [ord(char)]
                self._chord(codes)
                time.sleep(min(0.03, self._remaining()))
        return {"sent": True}

    def click(self, x, y, *, button="left"):
        if type(x) is not int or type(y) is not int:
            raise InvalidRequest("Click coordinates must be integers.")
        buttons = {"left": 1, "middle": 2, "right": 4}
        if not isinstance(button, str) or button not in buttons:
            raise InvalidRequest("button must be left, middle, or right.")
        with self._session():
            if not 0 <= x < self.width or not 0 <= y < self.height:
                raise InvalidRequest("Click coordinates leave the guest display.")
            self._before_input()
            self._input_started = True
            self._send(struct.pack(">BBHH", 5, 0, x, y))
            time.sleep(min(0.15, self._remaining()))
            try:
                self._send(struct.pack(">BBHH", 5, buttons[button], x, y))
                time.sleep(min(0.05, self._remaining()))
            finally:
                self._send(struct.pack(">BBHH", 5, 0, x, y))
        return {"sent": True}
