import json
import os
import tempfile
import unittest
from pathlib import Path
from reprolab.evaluator import evaluate
from reprolab.evidence import verify_bundle
from reprolab.fixtures import FIXTURES, cases
from reprolab.pipeline import reference_evaluation
from reprolab.probes import containment
from reprolab.sandbox import Sandbox


@unittest.skipUnless(os.environ.get("REPROLAB_INTEGRATION") == "true", "Requires an isolated gVisor-enabled Docker host")
class SandboxIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = Sandbox()
        cls.sandbox.ready()
        Path("reports").mkdir(exist_ok=True)

    def test_all_disclosed_bugs_fail_and_reference_repairs_pass_in_fresh_sandboxes(self):
        for fixture_id in FIXTURES:
            with self.subTest(fixture=fixture_id):
                value = reference_evaluation(fixture_id, self.sandbox)
                Path("reports/reference-" + fixture_id + ".json").write_text(json.dumps(value, indent=2))
                self.assertEqual(value["payload"]["status"], "verified", value["payload"]["evaluation"])
                self.assertNotEqual(value["payload"]["baseline"]["sandbox_id"], value["payload"]["evaluation"]["sandbox_id"])
                self.assertTrue(verify_bundle(value)["integrity"])

    def test_host_file_network_readonly_root_timeout_and_output_cap(self):
        with tempfile.TemporaryDirectory(prefix="reprolab-host-probe-") as directory:
            result = containment(self.sandbox, Path(directory))
        Path("reports/containment.json").write_text(json.dumps(result, indent=2))
        for name, evidence in result.items():
            self.assertTrue(evidence["ok"], (name, evidence))

    def test_a_candidate_cannot_pass_by_exiting_zero_or_printing_fake_success(self):
        inputs = cases("rounding", 73)
        for code in ["import sys\nsys.exit(0)\n", "def solve(args):\n    print('All tests passed')\n    return 0\n"]:
            outcome = self.sandbox.run(code, inputs)
            result = evaluate("rounding", inputs, outcome)
            self.assertFalse(result["valid_protocol"])
            self.assertEqual(result["passed"], 0)
            self.assertTrue(outcome["teardown"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
