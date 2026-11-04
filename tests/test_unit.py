from __future__ import annotations
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .helpers import execution
from reprolab.evaluator import evaluate
from reprolab.fixtures import FIXTURES, cases, expected
from reprolab.pipeline import run_job
from reprolab.provider import ProviderError, VultrProvider, parse_candidate
from reprolab.sandbox import IMAGE, Sandbox
from reprolab.store import AdmissionDenied, Store


class EvaluatorTests(unittest.TestCase):
    def test_fixture_oracles_cover_leap_year_rounding_and_duplicate_order(self):
        self.assertEqual(expected("month-end", {"date": "2028-01-31", "months": 1}), "2028-02-29")
        self.assertEqual(expected("rounding", {"items": [{"unit_minor": 1, "quantity": 1}], "discount_bps": 5000}), 1)
        self.assertEqual(expected("retry-events", {"events": [{"id": "a", "delta": 7}, {"id": "b", "delta": 2}, {"id": "b", "delta": 99}, {"id": "a", "delta": 99}]}), {"total": 9, "duplicates": ["b", "a"]})
        for fixture in FIXTURES:
            self.assertEqual(len(cases(fixture, 1)), 20)
            self.assertEqual(cases(fixture, 1), cases(fixture, 1))
            self.assertNotEqual(cases(fixture, 1), cases(fixture, 2))

    def test_exit_zero_and_pass_banner_cannot_pass(self):
        inputs = cases("rounding", 4)
        run = execution("rounding", inputs)
        run["stdout"] = "All tests passed"
        result = evaluate("rounding", inputs, run)
        self.assertFalse(result["valid_protocol"])
        self.assertEqual(result["passed"], 0)

    def test_stale_nonce_and_missing_results_fail_closed(self):
        inputs = cases("month-end", 4)
        for bad in ({"nonce": "stale", "results": []}, {"nonce": "test", "results": []}):
            run = execution("month-end", inputs); run["stdout"] = json.dumps(bad)
            self.assertFalse(evaluate("month-end", inputs, run)["valid_protocol"])

    def test_json_true_is_not_an_integer_one(self):
        inputs = [{"items": [{"unit_minor": 1, "quantity": 1}], "discount_bps": 0}]
        run = execution("rounding", inputs)
        value = json.loads(run["stdout"]); value["results"][0]["result"] = True
        run["stdout"] = json.dumps(value)
        self.assertEqual(evaluate("rounding", inputs, run)["failed"], 1)

    def test_unconfirmed_teardown_or_missing_policy_cannot_pass(self):
        inputs = cases("retry-events", 4)
        for field in ("teardown", "policy_verified"):
            run = execution("retry-events", inputs)
            run[field] = {"confirmed": False} if field == "teardown" else False
            self.assertFalse(evaluate("retry-events", inputs, run)["valid_protocol"])

    def test_missing_provider_never_uses_reference_code(self):
        class NeverRun:
            def ready(self): raise AssertionError("No sandbox should run")
        result = run_job("rounding", sandbox=NeverRun(), provider=VultrProvider(""), job_id="unit")
        self.assertEqual(result["payload"]["status"], "unavailable")
        self.assertNotIn("candidate", result["payload"])

    def test_candidate_requires_a_bounded_real_function(self):
        self.assertEqual(parse_candidate(json.dumps({"code": "def solve(args): return 1\n", "explanation": "Fix"}))["code"], "def solve(args): return 1\n")
        for content in ["Tests passed", '{"code":"print(1)","explanation":"Fix"}', '{"code":"def solve(:","explanation":"Fix"}']:
            with self.assertRaises(ProviderError): parse_candidate(content)

    def test_command_has_no_shell_host_env_network_or_mutable_image(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Sandbox(work_root=Path(directory))
            command = sandbox.command("reprolab-" + "a" * 32, Path(directory) / "work")
            self.assertIn("--runtime=runsc", command)
            self.assertIn("--network=none", command)
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--pull=never", command)
            self.assertIn(IMAGE, command)
            self.assertNotIn("VULTR_INFERENCE_API_KEY", " ".join(command))
            with self.assertRaises(ValueError): Sandbox(image="python:latest", work_root=Path(directory))


class StorageTests(unittest.TestCase):
    def test_atomic_admission_cannot_exceed_daily_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "jobs.sqlite", daily_limit=1)
            def request():
                try: return store.create("rounding")
                except AdmissionDenied: return None
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda i: request(), range(5)))
            self.assertEqual(sum(item is not None for item in results), 1)

    def test_capability_is_required_and_recovery_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "jobs.sqlite")
            job = store.create("month-end")
            self.assertIsNone(store.read(job["id"], "another-token"))
            self.assertEqual(store.read(job["id"], job["token"])["status"], "queued")
            store.recover()
            self.assertEqual(store.read(job["id"], job["token"])["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
