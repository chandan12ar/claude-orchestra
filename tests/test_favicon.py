"""The page declares its own icon, so browsers do not request /favicon.ico (a 404)."""

import os
import re
import unittest

INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "orchestra", "static", "index.html")


class TestFavicon(unittest.TestCase):
    def test_index_declares_inline_icon(self):
        with open(INDEX, encoding="utf-8") as fh:
            html = fh.read()
        match = re.search(r'<link rel="icon" href="(data:image/svg\+xml,[^"]+)"', html)
        self.assertIsNotNone(match, "index.html needs an inline rel=icon link")
        self.assertNotIn("<script", match.group(1).lower())


if __name__ == "__main__":
    unittest.main()
