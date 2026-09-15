"""Target lifecycle contracts: env injection, analytics sidecar, health, cap."""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLUGIN = os.path.join(ROOT, "target-plugin", "__init__.py")
MODELS = os.path.join(ROOT, "target-plugin", "models.py")


class TargetLifecycleContractTest(unittest.TestCase):
    def setUp(self):
        with open(PLUGIN, encoding="utf-8") as fh:
            self.src = fh.read()
        with open(MODELS, encoding="utf-8") as fh:
            self.models = fh.read()

    def test_model_has_isolation_fields(self):
        for field in ("challenge_key", "target_profile", "health_path"):
            self.assertIn(field, self.models)

    def test_challenge_env_injected(self):
        self.assertIn('"CHALLENGE_KEY"', self.src)
        self.assertIn('"TARGET_PROFILE"', self.src)

    def test_healthcheck_uses_configured_path(self):
        self.assertIn("def _healthcheck_spec", self.src)
        self.assertIn("healthcheck=_healthcheck_spec(internal_port", self.src)

    def test_analytics_gated_on_config(self):
        self.assertIn("if cfg.needs_analytics:", self.src)
        self.assertGreaterEqual(self.src.count("if cfg.needs_analytics:"), 2)

    def test_concurrent_cap(self):
        self.assertIn("Instance limit reached", self.src)
        self.assertIn("DEFAULT_MAX_CHALLENGE_INSTANCES", self.src)

    def test_challenge_container_naming(self):
        self.assertRegex(self.src, r"ctfd-chal-|chal_target")
        self.assertIn("_chal_target_name", self.src)
        self.assertIn("_chal_analytics_name", self.src)

    def test_stop_removes_analytics(self):
        self.assertIn("def _stop_challenge_stack", self.src)
        stop = self.src.split("def _stop_challenge_stack", 1)[1].split("def ", 1)[0]
        self.assertIn("_chal_analytics_name", stop)

    def test_defaults_needs_analytics_false(self):
        self.assertIn("needs_analytics = db.Column(db.Boolean, default=False", self.models)


if __name__ == "__main__":
    unittest.main()
