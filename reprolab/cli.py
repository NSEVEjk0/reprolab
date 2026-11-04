from __future__ import annotations
import argparse
import json
import sys
import uuid
from pathlib import Path
from .evidence import verify_bundle
from .evaluator import evaluate
from .fixtures import FIXTURES, cases
from .pipeline import reference_evaluation, run_job
from .probes import containment
from .provider import VultrProvider
from .sandbox import IMAGE, Sandbox


def main() -> None:
    parser = argparse.ArgumentParser(prog="reprolab")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fixtures")
    ready = commands.add_parser("doctor")
    ready.add_argument("--image", default=IMAGE)
    for name in ("run", "reference"):
        task = commands.add_parser(name)
        task.add_argument("fixture", choices=list(FIXTURES))
        task.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("evidence", type=Path)
    replay = commands.add_parser("replay")
    replay.add_argument("evidence", type=Path)
    probe = commands.add_parser("probes")
    probe.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "fixtures":
        print(json.dumps([{"id": f.id, "title": f.title, "description": f.description} for f in FIXTURES.values()], indent=2))
        return
    if args.command == "verify":
        print(json.dumps(verify_bundle(json.loads(args.evidence.read_text())), indent=2))
        return
    sandbox = Sandbox(image=args.image if args.command == "doctor" else IMAGE)
    if args.command == "doctor":
        print(json.dumps(sandbox.ready(), indent=2))
        return
    if args.command == "replay":
        value = json.loads(args.evidence.read_text())
        verify_bundle(value)
        payload = value["payload"]
        sandbox.ready()
        inputs = cases(payload["fixture_id"], payload["evaluation"]["seed"])
        result = evaluate(payload["fixture_id"], inputs, sandbox.run(payload["candidate"]["code"], inputs))
        print(json.dumps({"passed": result["passed"], "failed": result["failed"], "valid_protocol": result["valid_protocol"], "teardown": result["teardown"]}, indent=2))
        if not result["valid_protocol"] or result["failed"]:
            sys.exit(1)
        return
    if args.command == "probes":
        value = containment(sandbox, sandbox.work_root / "controller-only")
    elif args.command == "reference":
        value = reference_evaluation(args.fixture, sandbox)
        if value["payload"]["status"] != "verified":
            raise RuntimeError("Reference sandbox evaluation did not pass")
        verify_bundle(value)
    else:
        value = run_job(args.fixture, sandbox=sandbox, provider=VultrProvider.from_environment(), job_id=uuid.uuid4().hex)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2))
    print(str(args.output))


if __name__ == "__main__":
    main()
