import unittest

from infra.instance_manager.discovery import normalize_arch, os_family


class DiscoveryTest(unittest.TestCase):
    def test_arch_normalization(self):
        self.assertEqual(normalize_arch("x86_64"), "amd64")
        self.assertEqual(normalize_arch("AMD64"), "amd64")
        self.assertEqual(normalize_arch("aarch64"), "arm64")
        self.assertEqual(normalize_arch("arm64"), "arm64")

    def test_os_family_is_known_string(self):
        self.assertIn(os_family(), {"windows", "macos", "linux", "unknown"})


if __name__ == "__main__":
    unittest.main()
