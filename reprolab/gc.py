"""Independent watchdog for expired containers owned by this deployment."""
import json
import os
import sys
from pathlib import Path
from .sandbox import Sandbox

def main() -> None:
    state = Path(os.environ.get("REPROLAB_STATE_DIR", "./.local"))
    result = Sandbox(work_root=state / "work").collect()
    print(json.dumps(result))
    if not result["confirmed"]:
        sys.exit(1)

if __name__ == "__main__":
    main()
