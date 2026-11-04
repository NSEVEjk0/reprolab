from __future__ import annotations
import hashlib
import json
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def bundle(payload: dict[str, Any]) -> dict[str, Any]:
    raw = canonical(payload)
    return {"schema_version": 2, "payload": payload, "canonical_payload": raw.decode(), "sha256": sha256_bytes(raw)}


def verify_bundle(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != 2 or not isinstance(value.get("payload"), dict):
        raise ValueError("Unsupported evidence schema")
    payload = value["payload"]
    if value.get("canonical_payload") != canonical(payload).decode():
        raise ValueError("Canonical evidence bytes differ from the displayed payload")
    if value.get("sha256") != sha256_bytes(canonical(payload)):
        raise ValueError("Evidence was changed")
    from .fixtures import cases, expected
    source = payload.get("source")
    if not isinstance(source, str) or payload.get("source_sha256") != sha256_bytes(source.encode()):
        raise ValueError("Original source fingerprint mismatch")
    for key in ("baseline", "evaluation"):
        run = payload.get(key)
        if run is not None:
            if not run.get("teardown", {}).get("confirmed"):
                raise ValueError("Sandbox teardown is not confirmed")
            if run.get("runtime") != "runsc":
                raise ValueError("Execution did not use the required runsc runtime")
            if not run.get("policy_verified") or not run.get("policy") or not all(run["policy"].values()):
                raise ValueError("Required sandbox policy was not confirmed")
            inputs = cases(payload["fixture_id"], run["seed"])
            if run.get("inputs_sha256") != sha256_bytes(canonical(inputs)) or run.get("expected_sha256") != sha256_bytes(canonical([expected(payload["fixture_id"], args) for args in inputs])):
                raise ValueError("Independent case fingerprints differ")
            if run.get("case_count") != len(inputs) or run.get("passed", 0) + run.get("failed", 0) != len(inputs):
                raise ValueError("Case totals are inconsistent")
    candidate = payload.get("candidate")
    if candidate is not None and sha256_bytes(candidate["code"].encode()) != candidate["sha256"]:
        raise ValueError("Candidate fingerprint mismatch")
    if payload.get("baseline") and payload["baseline"].get("source_sha256") != payload["source_sha256"]:
        raise ValueError("The baseline did not run the original source")
    if payload.get("evaluation") and payload["evaluation"].get("source_sha256") != candidate["sha256"]:
        raise ValueError("The evaluated source differs from the candidate")
    if payload.get("status") == "verified":
        baseline, evaluation = payload["baseline"], payload["evaluation"]
        if not baseline.get("valid_protocol") or baseline.get("failed", 0) < 1:
            raise ValueError("A fix needs a reproduced original failure")
        if not evaluation.get("valid_protocol") or evaluation.get("failed") != 0 or evaluation.get("passed", 0) < 1:
            raise ValueError("The independent evaluation did not pass")
    return {"integrity": True, "status": payload.get("status"), "job_id": payload.get("job_id"), "controller_trust": "The hash detects changes; it does not attest the controller or host."}
