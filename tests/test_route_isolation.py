"""Route isolation: intended surfaces reachable; sibling routes blocked."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(ROOT, "challenges", "vbank-ctf")
sys.path.insert(0, APP_DIR)

from isolation import CHALLENGE_PREFIXES, PROFILE_KEYS, path_allowed


SIBLING_SAMPLES = {
    "sqli_login": "/search",
    "open_redirect": "/account/statements",
    "idor_statements": "/account/express-transfer",
    "idor_transfer": "/search",
    "race_condition": "/search",
    "reflected_xss": "/account/support",
    "xss": "/search",
    "staff_sqli": "/staff/maintenance",
    "jwt": "/api/v1/staff/onboard",
    "mass_assignment": "/staff/api-docs",
    "rce": "/staff/files",
    "path_traversal": "/staff/maintenance",
    "crypto_ecb": "/search",
    "ssrf": "/staff/payroll",
    "xxe": "/support/document-fetch",
    "proto_pollution": "/staff/payroll",
    "ssti": "/search",
}

INTENDED_SAMPLES = {
    "sqli_login": "/customer/login",
    "open_redirect": "/customer/login",
    "idor_statements": "/api/v1/accounts/1337/transactions",
    "idor_transfer": "/account/transfer/confirm",
    "race_condition": "/api/v1/transfer/express",
    "reflected_xss": "/search",
    "xss": "/staff/tickets",
    "staff_sqli": "/maintenance-portal",
    "jwt": "/api/v2/corporate/vault",
    "mass_assignment": "/api/v1/staff/onboard",
    "rce": "/staff/maintenance",
    "path_traversal": "/staff/files",
    "crypto_ecb": "/api/v1/receipts/TXN-SYSTEM",
    "ssrf": "/support/document-fetch",
    "xxe": "/staff/payroll",
    "proto_pollution": "/api/settings/merge",
    "ssti": "/account/profile",
}


class RouteIsolationTest(unittest.TestCase):
    def test_all_catalog_keys_have_surfaces(self):
        self.assertEqual(set(CHALLENGE_PREFIXES), set(INTENDED_SAMPLES))

    def test_common_routes_always_open(self):
        for key in CHALLENGE_PREFIXES:
            self.assertTrue(path_allowed("/", key, "auth"))
            self.assertTrue(path_allowed("/customer/login", key, "auth"))
            self.assertTrue(path_allowed("/robots.txt", key, "auth"))

    def test_intended_surface_open(self):
        for key, path in INTENDED_SAMPLES.items():
            self.assertTrue(path_allowed(path, key, "all"), msg=f"{key} should allow {path}")

    def test_sibling_surface_blocked(self):
        for key, path in SIBLING_SAMPLES.items():
            self.assertFalse(
                path_allowed(path, key, "all"),
                msg=f"{key} should block sibling {path}",
            )

    def test_profile_without_key_allows_group_only(self):
        self.assertTrue(path_allowed("/customer/login", "", "auth"))
        self.assertTrue(path_allowed("/search", "", "xss"))
        self.assertFalse(path_allowed("/search", "", "auth"))
        self.assertFalse(path_allowed("/staff/maintenance", "", "auth"))

    def test_open_profile_allows_all(self):
        self.assertTrue(path_allowed("/staff/files", "", "all"))
        self.assertTrue(path_allowed("/api/v2/corporate/vault", "", "vbank-ctf"))

    def test_ten_profiles(self):
        self.assertEqual(len(PROFILE_KEYS), 10)


if __name__ == "__main__":
    unittest.main()
