"""Exercise the real frozen updater without a GUI or remote-control services.

Builds two isolated executables and checks Windows replacement, rollback,
cancellation and signature enforcement. Never touches an installed app.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from markcontrol import updater as u


PROBE = r'''
import json, os, sys, time
from pathlib import Path
from markcontrol import updater as u
VERSION = "@VERSION@"
u.__version__ = VERSION
u.PUBLIC_KEY_HEX = "@PUBLIC@"
root = Path(os.environ["PROBE_ROOT"])
try:
    if sys.argv[1] == "--parent":
        (root / "parent-pid").write_text(str(os.getpid()))
        deadline = time.monotonic() + 120
        while not (root / "exit-parent").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    elif sys.argv[1] == "--apply-update":
        (root / "helper-started").touch()
        target, stage, version, pid = sys.argv[2:]
        result = u.apply(Path(target), Path(stage), version, int(pid))
        (root / "helper-status").write_text(str(result))
        raise SystemExit(result)
    elif sys.argv[1] == "--candidate":
        if VERSION == "0.6.0" and os.environ.get("PROBE_FAIL") == "exit":
            raise SystemExit(71)
        state = json.loads(os.environ[u.RESUME])
        (root / "result.json").write_text(json.dumps({
            "version": VERSION, "requested_version": state["version"],
            "active": state["active"], "headless": state["headless"],
            "window": state["window"], "pid": os.getpid()}))
        if state["version"] == VERSION:
            (Path(state["stage"]) / "ready").write_text(VERSION)
        deadline = time.monotonic() + 120
        while not (root / "exit-candidate").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
except Exception as exc:
    (root / "probe-error").write_text(type(exc).__name__)
    raise SystemExit(91)
'''


def wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Frozen update probe timed out")
        time.sleep(0.05)


def build(folder, version, public):
    name = "probe-" + version
    source = folder / (name + ".py")
    source.write_text(PROBE.replace("@VERSION@", version).replace("@PUBLIC@", public), encoding="utf-8")
    log = folder / (name + "-build.log")
    with log.open("w", encoding="utf-8") as output:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--noconsole",
                        "--name", name, "--distpath", str(folder / "dist"),
                        "--workpath", str(folder / "build"), "--specpath", str(folder),
                        "--paths", str(ROOT), str(source)], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return folder / "dist" / (name + ".exe")


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scenario(folder, old, new, key, mode):
    root = folder / mode
    root.mkdir()
    target = root / "app.exe"
    shutil.copy2(old, target)
    pool = u.pool(target)
    pool.mkdir()
    previous = pool / "previous.exe"
    shutil.copy2(old, previous)
    stage = pool / "release-probe"
    stage.mkdir()
    candidate = stage / u.WINDOWS
    shutil.copy2(new, candidate)
    with candidate.open("rb") as binary:
        digest, size = u.fingerprint(binary)
    fields = dict(version="0.6.0", name=u.WINDOWS, sha256=digest, size=size)
    fields["signature"] = key.sign(u.canonical(fields)).hex()
    (stage / "manifest").write_bytes(u.canonical(fields))
    if mode == "tampered":
        with candidate.open("ab") as binary:
            binary.write(b"tampered")
    env = dict(os.environ, PROBE_ROOT=str(root), LOCALAPPDATA=str(root / "profile"))
    env.pop(u.RESUME, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if mode == "rollback":
        env["PROBE_FAIL"] = "exit"
    flags = subprocess.CREATE_NO_WINDOW
    parent = subprocess.Popen([str(target), "--parent"], env=env, creationflags=flags)
    helper = None
    result = None
    try:
        wait_for(lambda: (root / "parent-pid").exists())
        pid = int((root / "parent-pid").read_text())
        with u.lease(directory=root / "profile" / "MarkControl"):
            helper = subprocess.Popen([str(previous), "--apply-update", str(target), str(stage), "0.6.0", str(pid)],
                                      env=env, creationflags=flags)
            wait_for(lambda: (root / "helper-started").exists())
            time.sleep(0.3)
            assert not (stage / "armed").exists(), "Helper armed during an active session lease"
        if mode == "tampered":
            assert helper.wait(20) != 0
            assert fingerprint(target) == fingerprint(old)
            assert (root / "probe-error").read_text() == "ValueError"
            return {"scenario": mode, "verified": True, "old_binary_preserved": True}
        wait_for(lambda: (stage / "armed").exists())
        if mode == "cancel":
            (stage / "cancel").touch()
            assert helper.wait(20) != 0
            assert fingerprint(target) == fingerprint(old)
            return {"scenario": mode, "verified": True, "old_binary_preserved": True}
        state = dict(active=True, headless=True, window="withdrawn", argv=["--candidate"])
        u.write_json(stage / "go", state)
        (root / "exit-parent").touch()
        parent.wait(20)
        assert helper.wait(45) == 0
        wait_for(lambda: (root / "result.json").exists())
        result = json.loads((root / "result.json").read_text())
        expected = old if mode == "rollback" else new
        assert fingerprint(target) == fingerprint(expected)
        assert result["version"] == ("0.5.0" if mode == "rollback" else "0.6.0")
        assert result["requested_version"] == ("" if mode == "rollback" else "0.6.0")
        assert result["active"] is True and result["headless"] is True and result["window"] == "withdrawn"
        return {"scenario": mode, "verified": True, "restored_version": result["version"],
                "active_preserved": True, "hidden_state_preserved": True}
    finally:
        (root / "exit-parent").touch()
        (root / "exit-candidate").touch()
        for process in (parent, helper):
            if process and process.poll() is None:
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    subprocess.run([Path(os.environ["SystemRoot"]) / "System32/taskkill.exe",
                                    "/PID", str(process.pid), "/T", "/F"], creationflags=flags,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This process probe specifically validates Windows executable replacement")
    folder = args.work.resolve() / ("run-" + uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw().hex()
    old, new = (build(folder, version, public) for version in ("0.5.0", "0.6.0"))
    results = []
    for mode in ("success", "rollback", "cancel", "tampered"):
        results.append(scenario(folder, old, new, key, mode))
        print(json.dumps(results[-1]), flush=True)
    (folder / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"results_file": str(folder / "results.json")}))


if __name__ == "__main__":
    main()
