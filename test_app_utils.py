import unittest
import sys
from unittest.mock import MagicMock

# Mocking Flask and other dependencies that are missing in the environment
# to allow importing from app.py
mock_flask = MagicMock()
mock_requests = MagicMock()
mock_yt_dlp = MagicMock()

sys.modules["flask"] = mock_flask
sys.modules["requests"] = mock_requests
sys.modules["yt_dlp"] = mock_yt_dlp
sys.modules["curl_cffi"] = MagicMock()
sys.modules["cv2"] = MagicMock()
sys.modules["imagehash"] = MagicMock()
sys.modules["PIL"] = MagicMock()
sys.modules["transformers"] = MagicMock()
sys.modules["torch"] = MagicMock()

# Now we can safely import from app
from app import _parse_cookie_string

class TestParseCookieString(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(_parse_cookie_string(""), {})
        self.assertEqual(_parse_cookie_string(None), {})

    def test_single_cookie(self):
        self.assertEqual(_parse_cookie_string("key=value"), {"key": "value"})

    def test_multiple_cookies(self):
        self.assertEqual(
            _parse_cookie_string("key1=value1; key2=value2"),
            {"key1": "value1", "key2": "value2"}
        )

    def test_whitespace_handling(self):
        self.assertEqual(
            _parse_cookie_string("  key1 = value1 ;  key2=value2  "),
            {"key1": "value1", "key2": "value2"}
        )

    def test_missing_equals(self):
        self.assertEqual(_parse_cookie_string("key1=value1; key2"), {"key1": "value1"})

    def test_multiple_equals(self):
        self.assertEqual(_parse_cookie_string("key=val=ue"), {"key": "val=ue"})

    def test_empty_key(self):
        self.assertEqual(_parse_cookie_string("=value; key=val"), {"key": "val"})

    def test_empty_value(self):
        self.assertEqual(_parse_cookie_string("key="), {"key": ""})

    def test_trailing_semicolon(self):
        self.assertEqual(_parse_cookie_string("key=value;"), {"key": "value"})

if __name__ == "__main__":
    unittest.main()
