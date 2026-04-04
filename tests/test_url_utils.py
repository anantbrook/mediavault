import unittest
from unittest.mock import MagicMock
import sys
import os

# Mock dependencies of app.py to avoid ImportError
mock_modules = [
    'flask', 'requests', 'curl_cffi', 'unblock_engine', 'url_resolver',
    'media_analyzer', 'upscaler', 'tagger', 'god_mode', 'yt_dlp',
    'cookie_manager', 'project_health', 'scheduler'
]
for module_name in mock_modules:
    sys.modules[module_name] = MagicMock()

# Now we can import the functions from app
from app import _url_path, is_direct_image, is_direct_video

class TestURLUtils(unittest.TestCase):
    def test_url_path(self):
        # Basic case
        self.assertEqual(_url_path("https://example.com/video.mp4"), "https://example.com/video.mp4")
        # Case insensitive
        self.assertEqual(_url_path("HTTPS://EXAMPLE.COM/VIDEO.MP4"), "https://example.com/video.mp4")
        # Strip query parameters
        self.assertEqual(_url_path("https://example.com/video.mp4?token=123&expire=456"), "https://example.com/video.mp4")
        # No path
        self.assertEqual(_url_path("https://example.com"), "https://example.com")
        # Path with dots but no extension
        self.assertEqual(_url_path("https://example.com/my.path/resource"), "https://example.com/my.path/resource")

    def test_is_direct_image(self):
        # Valid image extensions
        self.assertTrue(is_direct_image("https://example.com/image.jpg"))
        self.assertTrue(is_direct_image("https://example.com/image.JPEG"))
        self.assertTrue(is_direct_image("https://example.com/image.png"))
        self.assertTrue(is_direct_image("https://example.com/image.gif"))
        self.assertTrue(is_direct_image("https://example.com/image.webp"))
        self.assertTrue(is_direct_image("https://example.com/image.bmp"))
        self.assertTrue(is_direct_image("https://example.com/image.tiff"))
        self.assertTrue(is_direct_image("https://example.com/image.avif"))

        # Image with query parameters
        self.assertTrue(is_direct_image("https://example.com/image.png?width=800"))

        # Not images
        self.assertFalse(is_direct_image("https://example.com/video.mp4"))
        self.assertFalse(is_direct_image("https://example.com/page.html"))
        self.assertFalse(is_direct_image("https://example.com/image.jpg.txt"))
        self.assertFalse(is_direct_image("https://example.com/no_extension"))

    def test_is_direct_video(self):
        # Valid video extensions
        self.assertTrue(is_direct_video("https://example.com/video.mp4"))
        self.assertTrue(is_direct_video("https://example.com/video.WEBM"))
        self.assertTrue(is_direct_video("https://example.com/video.mkv"))
        self.assertTrue(is_direct_video("https://example.com/video.avi"))
        self.assertTrue(is_direct_video("https://example.com/video.mov"))
        self.assertTrue(is_direct_video("https://example.com/video.flv"))

        # Video with query parameters
        self.assertTrue(is_direct_video("https://example.com/video.mp4?quality=hd"))

        # Not videos
        self.assertFalse(is_direct_video("https://example.com/image.jpg"))
        self.assertFalse(is_direct_video("https://example.com/page.html"))
        self.assertFalse(is_direct_video("https://example.com/video.mp4.zip"))
        self.assertFalse(is_direct_video("https://example.com/no_extension"))

if __name__ == "__main__":
    unittest.main()
