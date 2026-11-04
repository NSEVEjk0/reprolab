import json
from pathlib import Path
from reprolab.fixtures import FIXTURES

Path("web/fixtures.json").write_text(json.dumps([{"id": f.id, "title": f.title, "description": f.description,
                                               "source": f.source, "example": f.example_input} for f in FIXTURES.values()], indent=2))
