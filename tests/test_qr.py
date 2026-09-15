"""Local QR encoder: join URLs must produce a scannable matrix."""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "target-plugin"))

import qr as qrlib  # noqa: E402


def _finder_ok(matrix, r0, c0):
    pat = [
        [1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1],
    ]
    for r in range(7):
        for c in range(7):
            if matrix[r0 + r][c0 + c] != pat[r][c]:
                return False
    return True


class QrEncoderTest(unittest.TestCase):
    def test_format_mask0_is_mask_pattern(self):
        self.assertEqual(qrlib._format_bits(0), 0x5412)

    def test_join_url_has_finders_and_dark_module(self):
        url = "http://192.168.0.8/register"
        matrix = qrlib.encode_matrix(url)
        n = len(matrix)
        self.assertGreaterEqual(n, 29)  # version 3 for this URL
        self.assertTrue(_finder_ok(matrix, 0, 0))
        self.assertTrue(_finder_ok(matrix, 0, n - 7))
        self.assertTrue(_finder_ok(matrix, n - 7, 0))
        self.assertEqual(matrix[8][n - 8], 1)
        self.assertEqual(matrix[6][8], 1)
        self.assertEqual(matrix[6][9], 0)

    def test_svg_contains_black_modules(self):
        svg = qrlib.svg_for_text("http://10.0.0.5/register")
        self.assertIn("<svg", svg)
        self.assertGreater(svg.count('fill="#000"'), 80)

    def test_different_texts_differ(self):
        a = qrlib.encode_matrix("http://192.168.0.8/register")
        b = qrlib.encode_matrix("http://192.168.0.9/register")
        self.assertNotEqual(a, b)

    def test_empty_svg(self):
        self.assertEqual(qrlib.svg_for_text(""), "")


if __name__ == "__main__":
    unittest.main()
