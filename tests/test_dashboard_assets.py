import unittest

from webui.assets import (ASSET_NAMES, INDEX_NAME, PUBLIC_ASSET_NAMES, PUBLIC_INDEX_NAME,
                          load_asset)


class DashboardAssetsTests(unittest.TestCase):
    def test_every_listed_asset_is_loaded_with_a_content_type(self):
        for name in ASSET_NAMES + PUBLIC_ASSET_NAMES + (INDEX_NAME, PUBLIC_INDEX_NAME):
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

    def test_public_page_links_only_public_assets(self):
        page = load_asset(PUBLIC_INDEX_NAME).body.decode("utf-8")
        self.assertIn("/static/public.js", page)
        self.assertNotIn("dashboard.js", page)

    def test_pages_have_no_inline_script_or_style(self):
        for name in (INDEX_NAME, PUBLIC_INDEX_NAME):
            page = load_asset(name).body.decode("utf-8")
            self.assertNotIn("<style", page, name)
            self.assertNotIn(" style=", page, name)
            self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)", name)

    def test_public_scripts_hold_no_organizer_endpoint(self):
        for name in PUBLIC_ASSET_NAMES:
            if name.endswith(".js"):
                self.assertNotIn("/api/action", load_asset(name).body.decode("utf-8"), name)
