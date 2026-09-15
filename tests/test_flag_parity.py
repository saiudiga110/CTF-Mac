"""Flag formula parity across CTFd, Flask, entrypoint, and analytics."""
import hashlib
import hmac
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def make_flag(secret, owner_id, flag_key):
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{owner_id}:{flag_key}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"LYD{{{digest}}}"


class FlagParityTest(unittest.TestCase):
    def test_known_vector(self):
        flag = make_flag("vbank_ctf_default_2024", "0", "sqli_login")
        self.assertTrue(flag.startswith("LYD{"))
        self.assertEqual(len(flag), 4 + 24 + 1)

    def test_flask_make_flag_matches(self):
        app_py = os.path.join(ROOT, "challenges", "vbank-ctf", "app.py")
        with open(app_py, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('f"{team_id}:{challenge}"', src)
        self.assertIn("LYD{", src)
        self.assertIn("hexdigest()[:24]", src)

    def test_vbank_flags_plugin_matches(self):
        path = os.path.join(ROOT, "ctfd", "plugins", "vbank_flags", "__init__.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('f"{team_id}:{challenge_key}"', src)
        self.assertIn("hexdigest()[:24]", src)
        self.assertIn("LYD{", src)

    def test_entrypoint_writes_same_formula(self):
        path = os.path.join(ROOT, "challenges", "vbank-ctf", "entrypoint.sh")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('f"{team}:{challenge}"', src)
        self.assertIn("make_flag('rce')", src)
        self.assertIn("make_flag('xxe')", src)
        self.assertIn("make_flag('ssti')", src)
        self.assertIn("make_flag('path_traversal')", src)
        self.assertIn("make_flag('idor_statements')", src)
        self.assertIn("make_flag('crypto_ecb')", src)

    def test_analytics_js_matches(self):
        path = os.path.join(ROOT, "challenges", "vbank-analytics", "app.js")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("createHmac('sha256'", src)
        self.assertIn("${TEAM_ID}:${challenge}", src)
        self.assertIn("slice(0, 24)", src)
        self.assertIn("makeFlag('proto_pollution')", src)

    def test_app_repairs_and_new_surfaces(self):
        path = os.path.join(ROOT, "challenges", "vbank-ctf", "app.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("@app.route('/search')", src)
        self.assertIn("X-Internal-Note", src)
        self.assertIn("@app.route('/maintenance-portal'", src)
        self.assertIn("make_flag('staff_sqli')", src)
        self.assertIn("make_flag('reflected_xss')", src)
        self.assertIn("SET balance = balance - ?", src)
        self.assertIn("compliance_status", src)
        self.assertIn("CHALLENGE_KEY", src)
        self.assertIn("TARGET_PROFILE", src)
        path = os.path.join(ROOT, "challenge-catalog.json")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("CTF{", src)
        self.assertNotIn("md5(vbank:", src)


if __name__ == "__main__":
    unittest.main()
