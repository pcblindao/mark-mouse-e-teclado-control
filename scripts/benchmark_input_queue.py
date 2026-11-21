"""Real loopback TLS/WSS with synthetic 20 ms input; never access the desktop."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("input_benchmark", ROOT / "tests" / "test_input_latency.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

results = []
for legacy, asynchronous in ((True, False), (False, False), (False, True)):
    with tempfile.TemporaryDirectory(prefix="markcontrol-input-benchmark-") as folder, MonkeyPatch.context() as patch:
        results.append(module.benchmark_input_burst(Path(folder), patch, asynchronous, legacy))
print(json.dumps({"scope": "loopback TLS/WSS, fake 20 ms native input; excludes real LAN/Wi-Fi, screen capture and OS input", "results": results}, indent=2))
