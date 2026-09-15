"""Catalog consistency: 7 Lloyds/vBank names/keys, images, seeders agree."""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "enhanced-features-plugin"))
sys.path.insert(0, os.path.join(ROOT, "target-plugin"))
sys.path.insert(0, os.path.join(ROOT, "ctfd", "setup"))

from challenge_catalog import canonical_names, challenges, image_tags, load_catalog


class CatalogConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(os.path.join(ROOT, "challenge-catalog.json"))
        self.rows = challenges(self.catalog)

    def test_count_is_seven(self):
        self.assertEqual(len(self.rows), 7)
        self.assertEqual(self.catalog.get("count"), 7)

    def test_unique_names_and_keys(self):
        names = [r["name"] for r in self.rows]
        keys = [r["flag_key"] for r in self.rows]
        self.assertEqual(len(set(names)), 7)
        self.assertEqual(len(set(keys)), 7)

    def test_exact_seven_names(self):
        expected = [
            "Trust, Not Verified",
            "Someone Else's Numbers",
            "The Account No One Touches",
            "Faster Than Careful",
            "Word For Word",
            "No Handle on Your Side",
            "Who's Really Typing",
        ]
        names = canonical_names(self.catalog)
        self.assertEqual(names, expected)

    def test_profile_image_tags(self):
        tags = image_tags(self.catalog)
        self.assertEqual(
            sorted(tags),
            sorted([
                "vbank-auth:1",
                "vbank-idor:1",
                "vbank-logic:1",
                "vbank-xss:1",
                "vbank-staff-auth:1",
                "vbank-staff-deep:1",
            ]),
        )
        self.assertEqual(len(set(tags)), 6)

    def test_no_analytics_sidecar_challenges(self):
        analytics = [r["name"] for r in self.rows if r.get("needs_analytics")]
        self.assertEqual(analytics, [])

    def test_required_fields(self):
        for row in self.rows:
            for field in (
                "name", "category", "value", "flag_key", "image", "profile",
                "internal_port", "health_path", "mem_limit", "description", "hints",
            ):
                self.assertTrue(row.get(field) not in (None, ""), msg=f"{row.get('name')} missing {field}")

    def test_setup_uses_catalog(self):
        path = os.path.join(ROOT, "ctfd", "setup", "setup.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("from challenge_catalog import", src)
        self.assertIn("for row in _CATALOG_ROWS", src)

    def test_seeder_matches_catalog(self):
        from challenge_seeder import CHALLENGES as seeder
        self.assertEqual([c["name"] for c in seeder], [r["name"] for r in self.rows])
        self.assertEqual([c["flag_key"] for c in seeder], [r["flag_key"] for r in self.rows])

    def test_compose_defines_active_profile_images(self):
        compose = os.path.join(ROOT, "docker-compose.yml")
        with open(compose, encoding="utf-8") as fh:
            src = fh.read()
        for tag in image_tags(self.catalog):
            self.assertIn(f"image: {tag}", src)
        plugin = os.path.join(ROOT, "enhanced-features-plugin", "challenge-catalog.json")
        if os.path.isfile(plugin):
            with open(os.path.join(ROOT, "challenge-catalog.json"), encoding="utf-8") as fh:
                root = json.load(fh)
            with open(plugin, encoding="utf-8") as fh:
                copy = json.load(fh)
            self.assertEqual(root, copy)


if __name__ == "__main__":
    unittest.main()
