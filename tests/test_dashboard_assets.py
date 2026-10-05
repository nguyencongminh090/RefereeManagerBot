import unittest

from webui.assets import ASSET_NAMES, INDEX_NAME, load_asset


class DashboardAssetsTests(unittest.TestCase):
    def test_every_listed_asset_is_loaded_with_a_content_type(self):
        for name in ASSET_NAMES:
            asset = load_asset(name)
            self.assertIsNotNone(asset, name)
            self.assertTrue(asset.body, name)
            self.assertIn("charset=utf-8", asset.content_type, name)

    def test_unknown_and_traversal_names_are_refused(self):
        for name in ("nope.css", "../assets.py", "..%2fassets.py", "", "static/dashboard.css"):
            self.assertIsNone(load_asset(name), name)

    def test_index_loads_the_page_that_links_its_own_assets(self):
        page = load_asset(INDEX_NAME).body.decode("utf-8")
        self.assertIn("/static/dashboard.css", page)
        self.assertIn("/static/dashboard.js", page)

    def test_page_has_no_inline_script_or_style(self):
        page = load_asset(INDEX_NAME).body.decode("utf-8")
        self.assertNotIn("<style", page)
        self.assertNotIn(" style=", page)
        self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)")
