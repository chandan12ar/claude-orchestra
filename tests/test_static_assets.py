import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")

EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", re.IGNORECASE)
FETCH_ABSOLUTE = re.compile(r"""fetch\(\s*["'](?:https?:)?//""", re.IGNORECASE)
LOCAL_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#]+)["']""")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class TestNoNetworkEgress(unittest.TestCase):
    """The local-only guarantee is a hard constraint; this is its enforcement."""

    def test_no_external_src_or_href(self):
        for name in ("index.html", "app.js", "style.css"):
            self.assertIsNone(EXTERNAL.search(read(name)),
                              "{} reaches an external host".format(name))

    def test_no_absolute_fetch(self):
        self.assertIsNone(FETCH_ABSOLUTE.search(read("app.js")))

    def test_no_font_imports(self):
        self.assertNotIn("@import", read("style.css"))


class TestLocalReferencesResolve(unittest.TestCase):
    def test_every_referenced_file_exists(self):
        for ref in LOCAL_REF.findall(read("index.html")):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, ref)),
                            "missing asset: {}".format(ref))


class TestPageStructure(unittest.TestCase):
    def test_required_mount_points_exist(self):
        html = read("index.html")
        for element_id in ("totals", "health", "timeline", "graph", "drawer",
                           "session-picker", "diagnostics"):
            self.assertIn('id="{}"'.format(element_id), html)

    def test_theme_is_defined_for_light_and_dark(self):
        css = read("style.css")
        self.assertIn(":root", css)
        self.assertIn("prefers-color-scheme: dark", css)

    def test_status_classes_exist_for_every_status(self):
        css = read("style.css")
        for status in ("running", "completed", "failed", "stalled",
                       "orphaned", "unknown"):
            self.assertIn(".s-{}".format(status), css)


class TestGraphAndDrawerPresent(unittest.TestCase):
    def test_functions_task_twelve_referenced_are_defined(self):
        js = read("app.js")
        for name in ("function renderGraph", "function openDrawer",
                     "function layoutGraph"):
            self.assertIn(name, js, "{} is missing".format(name))

    def test_inferred_edges_are_styled_differently(self):
        self.assertIn("edge-inferred", read("app.js"))
        self.assertIn(".edge-inferred", read("style.css"))

    def test_drawer_shows_the_extraction_source(self):
        self.assertIn("expected_output_source", read("app.js"))


if __name__ == "__main__":
    unittest.main()
