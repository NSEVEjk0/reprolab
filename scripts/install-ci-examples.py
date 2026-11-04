import json
from pathlib import Path
from reprolab.evidence import verify_bundle
from reprolab.fixtures import FIXTURES

target = Path("web/examples")
target.mkdir(parents=True, exist_ok=True)
for name in FIXTURES:
    source = Path("reports/reference-" + name + ".json")
    value = json.loads(source.read_text())
    verify_bundle(value)
    if value["payload"]["evidence_origin"] != "ci-reference-evaluation":
        raise RuntimeError("Expected explicitly disclosed reference evidence")
    (target / source.name).write_bytes(source.read_bytes())
