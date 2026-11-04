import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("solution", "/work/solution.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload = json.loads(sys.stdin.read())
results = []
for index, args in enumerate(payload["cases"]):
    try:
        result = module.solve(args)
        results.append({"index": index, "result": result, "error": None})
    except Exception as error:
        results.append({"index": index, "result": None, "error": type(error).__name__ + ": " + str(error)[:300]})
sys.stdout.write(json.dumps({"nonce": payload["nonce"], "results": results}, allow_nan=False))
