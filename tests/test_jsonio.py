import io
import json
import unittest
from unittest.mock import MagicMock

from vmware_guest_bridge.jsonio import loads
from vmware_guest_bridge.protocol import MAX_LINE, serve


class JsonTests(unittest.TestCase):
    def bridge(self):
        bridge = MagicMock()
        bridge.runner.redact.side_effect = lambda value: value
        return bridge

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            loads('{"action":"status","action":"quit"}')

    def test_nonfinite_guest_results_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(literal=literal), self.assertRaises(ValueError):
                loads('{"ok":true,"data":[' + literal + "]}")

    def test_nesting_is_bounded_before_redaction(self):
        with self.assertRaises(ValueError):
            loads("[" * 65 + "0" + "]" * 65)

    def test_utf8_byte_limit_not_character_limit(self):
        bridge = self.bridge()
        request = json.dumps(
            {"action": "console.type", "text": "Ж" * (MAX_LINE // 2)}, ensure_ascii=False
        )
        output = io.StringIO()
        self.assertEqual(serve(bridge, io.StringIO(request), output), 2)
        bridge.dispatch.assert_not_called()

    def test_invalid_utf8_does_not_dispatch(self):
        bridge = self.bridge()
        output = io.StringIO()
        serve(bridge, io.BytesIO(b'{"action":"status","id":"\xff"}\n'), output)
        self.assertFalse(json.loads(output.getvalue().splitlines()[1])["ok"])
        bridge.dispatch.assert_not_called()
