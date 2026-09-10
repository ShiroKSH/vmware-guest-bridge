import socket
import struct
import tempfile
import threading
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from vmware_guest_bridge.config import Config
from vmware_guest_bridge.console import Console, key_codes
from vmware_guest_bridge.errors import BridgeError, InvalidRequest, OutcomeUnknown

PIXELS = bytes((0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0))


def receive(connection, length):
    payload = bytearray()
    while len(payload) < length:
        part = connection.recv(length - len(payload))
        if not part:
            raise EOFError
        payload.extend(part)
    return bytes(payload)


def rectangle(x, y, width, height, payload, encoding=0):
    return struct.pack(">HHHHi", x, y, width, height, encoding) + payload


def update(*rectangles):
    return struct.pack(">BBH", 0, 0, len(rectangles)) + b"".join(rectangles)


class RfbServer:
    def __init__(self, *, version="003.008", dimensions=(2, 2), frames=None, security=1):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.version = version
        self.dimensions = dimensions
        self.frames = frames or [update(rectangle(0, 0, 2, 2, PIXELS))]
        self.security = security
        self.events = []
        self.errors = []
        self.connections = 0
        self.requests = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(4)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("Fixture server did not finish.")
        if self.errors:
            raise self.errors[0]

    def run(self):
        try:
            with self.listener.accept()[0] as connection:
                self.connections += 1
                connection.settimeout(3)
                self.handshake(connection)
                while True:
                    kind = receive(connection, 1)[0]
                    if kind == 3:
                        receive(connection, 9)
                        self.requests += 1
                        for frame in self.frames:
                            connection.sendall(frame)
                    elif kind == 4:
                        down, _, code = struct.unpack(">BHI", receive(connection, 7))
                        self.events.append(("key", code, down))
                    elif kind == 5:
                        button, x, y = struct.unpack(">BHH", receive(connection, 5))
                        self.events.append(("pointer", button, x, y))
                    else:
                        raise AssertionError(f"Unexpected client message {kind}")
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass
        except BaseException as exc:
            self.errors.append(exc)

    def handshake(self, connection):
        banner = f"RFB {self.version}\n".encode()
        # Deliberately fragment the handshake across TCP writes.
        for offset in range(0, len(banner), 3):
            connection.sendall(banner[offset : offset + 3])
        assert receive(connection, 12) == banner
        if self.version == "003.003":
            connection.sendall(struct.pack(">I", self.security))
        else:
            connection.sendall(bytes([1, self.security]))
            assert receive(connection, 1) == b"\x01"
            if self.version == "003.008":
                connection.sendall(b"\x00" * 4)
        assert receive(connection, 1) == b"\x01"
        name = b"Fixture desktop"
        connection.sendall(
            struct.pack(">HH", *self.dimensions) + bytes(16) + struct.pack(">I", len(name)) + name
        )
        pixel_format = receive(connection, 20)
        assert pixel_format[:4] == bytes(4)
        assert pixel_format[4:8] == bytes((32, 24, 0, 1))
        assert receive(connection, 8) == struct.pack(">BBHi", 2, 0, 1, 0)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(host_root=self.root, timeout=1, allow_unauthenticated=True)

    def client(self, server):
        return Console(replace(self.config, console_port=server.port))

    def test_opt_in_checked_before_socket_creation(self):
        console = Console(replace(self.config, allow_unauthenticated=False))
        with (
            patch("vmware_guest_bridge.console.socket.create_connection") as connect,
            self.assertRaises(InvalidRequest),
        ):
            console.probe()
        connect.assert_not_called()

    def test_all_supported_protocol_versions(self):
        for version in ("003.003", "003.007", "003.008"):
            with self.subTest(version=version), RfbServer(version=version) as server:
                console = self.client(server)
                try:
                    self.assertEqual(
                        console.probe(),
                        {
                            "width": 2,
                            "height": 2,
                            "desktop_name": "Fixture desktop",
                        },
                    )
                finally:
                    console.close()

    def test_png_contains_complete_frame_and_correct_colors(self):
        frames = [
            update(),
            update(rectangle(0, 0, 2, 1, PIXELS[:8])),
            update(rectangle(0, 1, 2, 1, PIXELS[8:])),
        ]
        with RfbServer(frames=frames) as server:
            console = self.client(server)
            try:
                result = console.screenshot("screen.png")
            finally:
                console.close()
        encoded = Path(result["path"]).read_bytes()
        self.assertTrue(encoded.startswith(b"\x89PNG\r\n\x1a\n"))
        offset = 8
        compressed = bytearray()
        while offset < len(encoded):
            size = struct.unpack(">I", encoded[offset : offset + 4])[0]
            kind = encoded[offset + 4 : offset + 8]
            payload = encoded[offset + 8 : offset + 8 + size]
            crc = struct.unpack(">I", encoded[offset + 8 + size : offset + 12 + size])[0]
            self.assertEqual(zlib.crc32(kind + payload), crc)
            if kind == b"IDAT":
                compressed.extend(payload)
            offset += 12 + size
        self.assertEqual(
            zlib.decompress(compressed),
            bytes((0, 255, 0, 0, 0, 255, 0, 0, 0, 0, 255, 255, 255, 255)),
        )

    def test_input_reuses_connection_and_releases_keys_and_button(self):
        with RfbServer() as server:
            console = self.client(server)
            try:
                console.press("Ctrl+Alt+Delete")
                console.click(1, 1)
            finally:
                console.close()
        self.assertEqual(server.connections, 1)
        self.assertEqual(server.requests, 1)
        self.assertEqual(
            server.events[:6],
            [
                ("key", 0xFFE3, 1),
                ("key", 0xFFE9, 1),
                ("key", 0xFFFF, 1),
                ("key", 0xFFFF, 0),
                ("key", 0xFFE9, 0),
                ("key", 0xFFE3, 0),
            ],
        )
        self.assertEqual(
            server.events[6:],
            [
                ("pointer", 0, 1, 1),
                ("pointer", 1, 1, 1),
                ("pointer", 0, 1, 1),
            ],
        )

    def test_each_capture_connects_afresh_without_reusing_input_session(self):
        console = Console(self.config)
        with RfbServer() as first:
            console.config = replace(self.config, console_port=first.port)
            console.screenshot("first.png")
        with RfbServer() as second:
            console.config = replace(self.config, console_port=second.port)
            console.screenshot("second.png")
        self.assertEqual(first.requests, 1)
        self.assertEqual(second.requests, 1)
        self.assertEqual(
            (self.root / "first.png").read_bytes(), (self.root / "second.png").read_bytes()
        )

    def test_crlf_sends_only_one_enter(self):
        with RfbServer() as server:
            console = self.client(server)
            try:
                console.type_text("\r\n")
            finally:
                console.close()
        self.assertEqual(server.events, [("key", 0xFF0D, 1), ("key", 0xFF0D, 0)])

    def test_shifted_text(self):
        with RfbServer() as server:
            console = self.client(server)
            try:
                console.type_text("A!")
            finally:
                console.close()
        self.assertEqual(
            server.events,
            [
                ("key", 0xFFE1, 1),
                ("key", ord("a"), 1),
                ("key", ord("a"), 0),
                ("key", 0xFFE1, 0),
                ("key", 0xFFE1, 1),
                ("key", ord("1"), 1),
                ("key", ord("1"), 0),
                ("key", 0xFFE1, 0),
            ],
        )

    def test_malformed_rectangle_does_not_publish_screenshot(self):
        with RfbServer(frames=[update(rectangle(1, 1, 2, 2, PIXELS))]) as server:
            console = self.client(server)
            with self.assertRaises(BridgeError):
                console.screenshot("bad.png")
            self.assertIsNone(console._socket)
        self.assertFalse((self.root / "bad.png").exists())

    def test_oversized_dimensions_rejected_during_handshake(self):
        with RfbServer(dimensions=(8192, 8192)) as server:
            console = self.client(server)
            with self.assertRaises(BridgeError):
                console.probe()
            self.assertIsNone(console._socket)

    def test_unsupported_authentication_closes_connection(self):
        with RfbServer(security=2) as server:
            console = self.client(server)
            with self.assertRaisesRegex(BridgeError, "authentication"):
                console.probe()
            self.assertIsNone(console._socket)

    def test_partial_screen_times_out_instead_of_returning_black_pixels(self):
        with RfbServer(frames=[update(rectangle(0, 0, 2, 1, PIXELS[:8]))]) as server:
            console = self.client(server)
            with self.assertRaises(BridgeError):
                console.screenshot("partial.png")
        self.assertFalse((self.root / "partial.png").exists())

    def test_transport_failure_after_input_is_unknown_outcome(self):
        with RfbServer() as server:
            console = self.client(server)
            original_send = console._send

            def fail_on_key(payload):
                if payload[0] == 4:
                    raise ConnectionResetError("fixture disconnect")
                original_send(payload)

            with (
                patch.object(console, "_send", side_effect=fail_on_key),
                self.assertRaises(OutcomeUnknown),
            ):
                console.press("Enter")
            self.assertIsNone(console._socket)
        self.assertEqual(server.connections, 1)

    def test_invalid_text_and_chords_rejected_before_connect(self):
        console = Console(self.config)
        for text in ("", "текст", "\x1b", "x" * 1025):
            with self.subTest(text=text[:8]), self.assertRaises(InvalidRequest):
                console.type_text(text)
        for chord in ("Ctrl+Ctrl", "Ctrl++", "Unknown", ""):
            with self.subTest(chord=chord), self.assertRaises(InvalidRequest):
                key_codes(chord)
