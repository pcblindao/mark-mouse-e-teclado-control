"""Prove real cross-process locks and IPC without opening a GUI or installed app."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from markcontrol import installation


PROBE = r'''
import json, os, sys, time
from pathlib import Path
from markcontrol import installation as i
root = Path(os.environ["PROOF_ROOT"])
folder = root / "profile"
# No control server is started or contacted by this proof.
i.product_port_busy = lambda: False
startup = i.acquire_desktop(folder)
owner = startup.instance is not None
(root / ("member-" + str(os.getpid()) + ".json")).write_text(json.dumps({"owner": owner, "exit_code": startup.exit_code}))
try:
    if owner:
        i.mark_ready()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            actions = list(i.actions())
            if "shutdown" in actions:
                (root / "shutdown-confirmed").touch()
                break
            time.sleep(0.02)
    elif startup.exit_code != 0:
        raise SystemExit(startup.exit_code)
finally:
    startup.close()
'''


def wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Single-instance process proof timed out")
        time.sleep(0.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    if not 2 <= args.count <= 32:
        parser.error("Use 2 to 32 processes")
    root = args.work.resolve() / ("single-instance-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    folder = root / "profile"
    folder.mkdir()
    fixture = {"config.json": b'{"agent_enabled":true}',
               "diagnostic-consent.json": b'{"version":2,"enabled":true}',
               "identity.pem": b"identity fixture", "trust.json": b'{}'}
    before = {}
    for name, content in fixture.items():
        (folder / name).write_bytes(content)
        before[name] = hashlib.sha256(content).hexdigest()
    source = root / "probe.py"
    source.write_text(PROBE, encoding="utf-8")
    env = dict(os.environ, PROOF_ROOT=str(root), PYTHONPATH=str(ROOT),
               LOCALAPPDATA=str(root / "isolated-local"), XDG_DATA_HOME=str(root / "isolated-local"))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes = []
    try:
        for _ in range(args.count):
            processes.append(subprocess.Popen([sys.executable, str(source)], cwd=ROOT, env=env,
                                               creationflags=flags))
        wait(lambda: len(list(root.glob("member-*.json"))) == args.count)
        reports = [json.loads(path.read_text()) for path in root.glob("member-*.json")]
        assert sum(item["owner"] for item in reports) == 1
        assert all(item["exit_code"] in (None, 0) for item in reports)
        assert installation.notify(folder, "shutdown")["accepted"] is True
        wait(lambda: (root / "shutdown-confirmed").exists())
        for process in processes:
            assert process.wait(timeout=10) == 0
        after = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in fixture}
        assert before == after
        probe = installation.FileLock(folder / "desktop-instance.lock")
        assert probe.acquire()
        probe.close()
        result = {"launches": args.count, "desktop_owners": 1,
                  "secondary_handoffs": args.count - 1, "authenticated_shutdown": True,
                  "preferences_byte_preserved": True, "lock_released_after_exit": True,
                  "gui_or_installed_app_touched": False}
        (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        installation.notify(folder, "shutdown")
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Only Popen handles created by this isolated proof.
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
