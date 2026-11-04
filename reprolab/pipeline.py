from __future__ import annotations
import secrets
from datetime import datetime, timezone
from typing import Any, Callable
from .evidence import bundle, sha256_bytes
from .evaluator import evaluate
from .fixtures import FIXTURES, cases
from .provider import ProviderError, ProviderUnavailable, VultrProvider
from .sandbox import Sandbox, SandboxUnavailable


def run_job(fixture_id: str, *, sandbox: Sandbox, provider: VultrProvider,
            job_id: str, progress: Callable[[str, str], None] = lambda stage, message: None) -> dict[str, Any]:
    fixture = FIXTURES[fixture_id]
    payload: dict[str, Any] = {"job_id": job_id, "fixture_id": fixture_id, "title": fixture.title,
                               "source": fixture.source, "source_sha256": fixture.sha256,
                               "created_at": datetime.now(timezone.utc).isoformat(), "attempts": [],
                               "status": "unavailable", "evidence_origin": "live-vultr-job"}
    try:
        if not provider.configured:
            raise ProviderUnavailable("Connect Vultr Serverless Inference before starting a live job")
        payload["environment"] = sandbox.ready()
        progress("reproducing", "Running the original code in a disposable sandbox")
        seed = secrets.randbits(48)
        baseline_inputs = cases(fixture_id, seed)
        baseline = evaluate(fixture_id, baseline_inputs, sandbox.run(fixture.source, baseline_inputs))
        baseline["seed"] = seed
        payload["baseline"] = baseline
        if not baseline["valid_protocol"] or baseline["failed"] < 1:
            payload.update(status="failed", message="The reported original failure could not be reproduced with a valid sandbox result")
            return bundle(payload)
        previous = None
        failure = baseline["failures"][0]
        for attempt in range(1, 3):
            progress("planning" if attempt == 1 else "retrying", "Vultr inference is proposing a bounded repair" if attempt == 1 else "One bounded retry using observed evaluation feedback")
            candidate = provider.propose(fixture, failure, previous)
            payload["candidate"] = candidate
            progress("testing", "Testing the candidate against new independent cases in a fresh sandbox")
            evaluation_seed = secrets.randbits(48)
            inputs = cases(fixture_id, evaluation_seed)
            evaluation = evaluate(fixture_id, inputs, sandbox.run(candidate["code"], inputs))
            evaluation["seed"] = evaluation_seed
            payload["attempts"].append({"attempt": attempt, "candidate_sha256": candidate["sha256"], "provider": candidate["provider"], "evaluation": evaluation})
            payload["evaluation"] = evaluation
            if evaluation["valid_protocol"] and evaluation["failed"] == 0:
                payload.update(status="verified", message="The original failure was reproduced and this candidate passed all declared independent cases")
                return bundle(payload)
            if not evaluation.get("teardown", {}).get("confirmed") or not evaluation.get("policy_verified"):
                break
            previous = candidate["code"]
            failure = evaluation["failures"][0] if evaluation["failures"] else {"error": evaluation.get("protocol_error", "Invalid worker protocol")}
        payload.update(status="failed", message="No candidate passed within the two-attempt budget")
    except (ProviderUnavailable, SandboxUnavailable) as error:
        payload.update(status="unavailable", message=str(error))
    except ProviderError as error:
        payload.update(status="failed", message=str(error))
    return bundle(payload)


def reference_evaluation(fixture_id: str, sandbox: Sandbox) -> dict[str, Any]:
    """Explicit CI/replay evidence; never called by the live job API."""
    fixture = FIXTURES[fixture_id]
    seed = 61104
    inputs = cases(fixture_id, seed)
    baseline = evaluate(fixture_id, inputs, sandbox.run(fixture.source, inputs))
    evaluation_seed = 61105
    evaluation_inputs = cases(fixture_id, evaluation_seed)
    evaluation = evaluate(fixture_id, evaluation_inputs, sandbox.run(fixture.reference, evaluation_inputs))
    baseline["seed"], evaluation["seed"] = seed, evaluation_seed
    status = "verified" if baseline["valid_protocol"] and baseline["failed"] > 0 and evaluation["valid_protocol"] and evaluation["failed"] == 0 else "failed"
    return bundle({"job_id": "ci-reference-" + fixture_id, "fixture_id": fixture_id, "title": fixture.title,
                   "source": fixture.source, "source_sha256": fixture.sha256, "created_at": datetime.now(timezone.utc).isoformat(),
                   "status": status, "evidence_origin": "ci-reference-evaluation", "environment": sandbox.ready(),
                   "candidate": {"code": fixture.reference, "sha256": sha256_bytes(fixture.reference.encode()),
                                  "explanation": "Disclosed reference repair used to test the sandbox and evaluator; no model was called", "provider": {"name": "reference-fixture", "model": None}},
                   "baseline": baseline, "evaluation": evaluation, "attempts": [],
                   "message": "Real sandbox execution of a disclosed reference patch; this is not an autonomous AI run"})
