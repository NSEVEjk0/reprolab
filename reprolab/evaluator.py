"""Expected outputs and result acceptance stay outside generated-code execution."""
from __future__ import annotations
import json
from typing import Any
from .evidence import canonical, sha256_bytes
from .fixtures import expected


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON values are not accepted")


def evaluate(fixture_id: str, inputs: list[dict[str, Any]], execution: dict[str, Any]) -> dict[str, Any]:
    record = {key: value for key, value in execution.items() if key != "stdout"}
    expected_values = [expected(fixture_id, args) for args in inputs]
    record.update({"case_count": len(inputs), "inputs_sha256": sha256_bytes(canonical(inputs)),
                   "expected_sha256": sha256_bytes(canonical(expected_values)), "passed": 0,
                   "failed": len(inputs), "valid_protocol": False, "failures": []})
    if execution.get("stop_reason") or execution.get("exit_code") != 0:
        record["protocol_error"] = "Sandbox did not complete normally"
        return record
    if not execution.get("policy_verified") or not execution.get("teardown", {}).get("confirmed"):
        record["protocol_error"] = "Sandbox policy or teardown was not verified"
        return record
    try:
        payload = json.loads(execution.get("stdout", ""), parse_constant=reject_constant)
        if not isinstance(payload, dict) or set(payload) != {"nonce", "results"}:
            raise ValueError("Expected the worker result protocol")
        if payload["nonce"] != execution["nonce"] or not isinstance(payload["results"], list) or len(payload["results"]) != len(inputs):
            raise ValueError("The response does not belong to these cases")
        for index, (item, wanted) in enumerate(zip(payload["results"], expected_values, strict=True)):
            if not isinstance(item, dict) or set(item) != {"index", "result", "error"} or type(item["index"]) is not int or item["index"] != index:
                raise ValueError("Invalid per-case result")
            if item["error"] is not None and not isinstance(item["error"], str):
                raise ValueError("Invalid per-case error")
            passed = item["error"] is None and canonical(item["result"]) == canonical(wanted)
            if passed:
                record["passed"] += 1
            elif len(record["failures"]) < 4:
                record["failures"].append({"input": inputs[index], "expected": wanted, "actual": item["result"], "error": item["error"]})
        record["failed"] = len(inputs) - record["passed"]
        record["valid_protocol"] = True
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        record.update({"passed": 0, "failed": len(inputs), "protocol_error": str(error)[:300], "failures": []})
    return record
