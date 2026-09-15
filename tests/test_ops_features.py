"""Operations feature contracts: board, pulse, pack, probe, helpdesk."""
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLUGIN = os.path.join(ROOT, "target-plugin", "__init__.py")
OPS = os.path.join(ROOT, "target-plugin", "templates", "ops.html")
BOARD = os.path.join(ROOT, "target-plugin", "templates", "board.html")
PACK = os.path.join(ROOT, "target-plugin", "templates", "pack.html")
BRIEFING = os.path.join(ROOT, "target-plugin", "templates", "briefing.html")


class OpsFeatureContractTest(unittest.TestCase):
    def setUp(self):
        with open(PLUGIN, encoding="utf-8") as fh:
            self.src = fh.read()
        with open(OPS, encoding="utf-8") as fh:
            self.ops = fh.read()

    def test_public_board_routes(self):
        self.assertIn('.route("/board"', self.src)
        self.assertIn('.route("/board.json"', self.src)
        self.assertIn("def _board_authorized", self.src)
        self.assertTrue(os.path.isfile(BOARD))

    def test_print_pack_and_qr(self):
        self.assertIn('.route("/admin/ops/pack"', self.src)
        self.assertIn("qr_svg=_qr_svg(join_url)", self.src)
        self.assertTrue(os.path.isfile(PACK))
        with open(BRIEFING, encoding="utf-8") as fh:
            briefing = fh.read()
        self.assertIn("qr_svg|safe", briefing)

    def test_event_day_routes(self):
        for route in (
            "/admin/ops/pulse",
            "/admin/ops/probe",
            "/admin/ops/preflight",
            "/admin/ops/player",
            "/admin/ops/stop-user",
            "/admin/ops/extend-all",
            "/admin/ops/join-password",
            "/admin/ops/board-token",
            "/admin/ops/backups/delete",
        ):
            self.assertIn('.route("%s"' % route, self.src, msg=route)

    def test_ops_ui_hooks(self):
        for needle in (
            "Projector",
            "Player pack",
            "btn-preflight",
            "btn-probe",
            "btn-extend-all",
            "btn-player-lookup",
            "ops-pulse",
        ):
            self.assertIn(needle, self.ops, msg=needle)

    def test_print_pages_skip_theme(self):
        self.assertIn('path.startswith("/plugins/ctfd-target/board")', self.src)
        self.assertIn('path.endswith("/briefing")', self.src)
        self.assertIn('path.endswith("/pack")', self.src)


if __name__ == "__main__":
    unittest.main()
