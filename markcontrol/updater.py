"""Atualização do executável instalado no perfil do usuário."""
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext, suppress
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .certs import data_dir
from .config import write_bytes, write_json
from .version import __version__

REPO = "pcblindao/mark-mouse-e-teclado-control"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PUBLIC_KEY_HEX = ""  # Cole aqui a saída pública de sign_release.py --generate.
LIMIT = 200_000_000
INTERVAL = 6 * 3600
WINDOWS = "markcontrol-windows-x64.exe"
LINUX = "markcontrol-linux-x64"
RESUME = "MARKCONTROL_UPDATE_STATE"


def parts(version):
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}", version):
        raise ValueError("Versão inválida")
    return tuple(map(int, version.split(".")))


def canonical(fields):
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def fingerprint(stream):
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(131072), b""):
        size += len(chunk)
        if size > LIMIT:
            raise ValueError("Arquivo muito grande")
        digest.update(chunk)
    return digest.hexdigest(), size


def manifest(raw, version, name):
    fields = json.loads(raw)
    signature = bytes.fromhex(fields.pop("signature"))
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY_HEX))
    key.verify(signature, canonical(fields))
    if (set(fields) != {"version", "name", "sha256", "size"} or fields["name"] != name
            or fields["version"] != version or parts(version) <= parts(__version__)
            or type(fields["size"]) is not int or not 0 < fields["size"] <= LIMIT
            or not re.fullmatch("[0-9a-f]{64}", fields["sha256"])):
        raise ValueError("Manifesto inválido")
    return fields


def verify(stream, fields):
    if fingerprint(stream) != (fields["sha256"], fields["size"]):
        raise ValueError("Arquivo diferente do manifesto")


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dest = urlsplit(newurl)
        if dest.scheme != "https" or dest.netloc not in {
            "github.com", "api.github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        }:
            raise ValueError("Destino de release inválido")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url, limit, output, checkpoint):
    request = Request(url, headers={"User-Agent": "markcontrol/" + __version__})
    checkpoint()
    with build_opener(Redirects()).open(request, timeout=15) as response:
        remaining = limit
        while True:
            checkpoint()
            chunk = response.read(min(131072, remaining + 1))
            if not chunk:
                return
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("Download excedeu o limite")
            output.write(chunk)


def small(url, limit, checkpoint):
    out = io.BytesIO()
    read_url(url, limit, out, checkpoint)
    return out.getvalue()


@contextmanager
def lease(exclusive=False, directory=None):
    # Uma leitura por sessão, inclusive entre processos. A troca exige exclusividade.
    folder = directory or data_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / "sessions.db", timeout=0 if exclusive else 0.25)) as db:
        db.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
        db.execute("SELECT count(*) FROM sqlite_master").fetchone()
        yield


def session_lease():
    return lease() if getattr(sys, "frozen", False) else nullcontext()


def pool(target):
    return target.parent / ("." + target.name + ".updates")


def installed():
    if not getattr(sys, "frozen", False) or platform.machine().lower() not in ("amd64", "x86_64"):
        return None
    target = Path(sys.executable).resolve()
    if sys.platform == "win32":
        folder = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "MarkControl"
        if target.parent != folder.resolve():
            return None
    elif sys.platform != "linux":
        return None
    return target


def asset():
    return WINDOWS if os.name == "nt" else LINUX


def spawn(executable, args, **kwargs):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    env.pop(RESUME, None)
    env.update(kwargs.pop("extra_env", {}))
    return subprocess.Popen([str(executable), *args], env=env, **kwargs)


def prepare(target, cancel):
    def checkpoint():
        while not cancel.is_set():
            try:
                with lease(exclusive=True):
                    return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                cancel.wait(0.5)
        raise InterruptedError()

    release = json.loads(small(API, 1 << 20, checkpoint))
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not tag.startswith("v"):
        return
    version = tag[1:]
    if parts(version) <= parts(__version__):
        return
    name = asset()
    base = f"https://github.com/{REPO}/releases/download/{tag}/"
    for filename in (name, name + ".sig"):
        if [a["browser_download_url"] for a in release["assets"] if a["name"] == filename] != [base + filename]:
            return
    raw = small(base + name + ".sig", 4096, checkpoint)
    fields = manifest(raw, version, name)
    folder = pool(target)
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or (os.name != "nt" and
            (folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077)):
        raise ValueError("Diretório de atualização inseguro")
    stage = Path(tempfile.mkdtemp(dir=folder, prefix="release-"))
    try:
        candidate = stage / name
        with candidate.open("x+b") as out:
            read_url(base + name, fields["size"], out, checkpoint)
            out.seek(0)
            verify(out, fields)
        candidate.chmod(0o700)
        (stage / "manifest").write_bytes(raw)
        previous = folder / ("previous" + target.suffix)
        while True:
            checkpoint()
            try:
                with lease(exclusive=True):
                    if sys.platform == "linux" and not target.samefile("/proc/self/exe"):
                        return
                    shutil.copy2(target, stage / "backup")
                    os.replace(stage / "backup", previous)
                    process = spawn(previous, ["--apply-update", str(target), str(stage), version, str(os.getpid())])
                    break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
        result = stage, process
        stage = None  # O auxiliar passa a responder pela limpeza.
        return result
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def parent_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return kernel.WaitForSingleObject(wintypes.HANDLE(handle), 0) == 258
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def replace(source, target):
    def attempt():
        try:
            os.replace(source, target)
            return True
        except PermissionError:
            return False
    if not wait_for(attempt, 30):
        raise OSError("Executável ocupado")


def stop_candidate(process):
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run([Path(os.environ["SystemRoot"]) / "System32/taskkill.exe",
                            "/T", "/F", "/PID", str(process.pid)], check=False,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


def swap(target, previous, stage, state, version):
    def restart(updated):
        resume = dict(state, version=version if updated else "", stage=str(stage))
        return spawn(target, state["argv"], extra_env={RESUME: json.dumps(resume)},
                     start_new_session=os.name != "nt")

    changed = False
    process = None
    healthy = False
    try:
        replace(stage / asset(), target)
        changed = True
        process = restart(True)
        ready = stage / "ready"
        wait_for(lambda: process.poll() is not None or ready.exists(), 30)
        healthy = process.poll() is None and ready.is_file() and ready.read_text() == version
    finally:
        if changed and not healthy:
            if process:
                stop_candidate(process)
            shutil.copy2(previous, stage / "restore")
            replace(stage / "restore", target)
            restart(False)
        elif not changed:
            restart(False)


def apply(target, stage, version, pid):
    previous = Path(sys.executable).resolve()
    if (stage.parent != pool(target) or previous.parent != stage.parent
            or previous.name != "previous" + target.suffix or not stage.name.startswith("release-")):
        return 2
    try:
        def cancelled():
            return (stage / "cancel").exists() or not parent_alive(pid)

        while not cancelled():
            try:
                with lease(exclusive=True):
                    if sys.platform == "linux" and not previous.samefile("/proc/self/exe"):
                        return 1
                    fields = manifest((stage / "manifest").read_bytes(), version, asset())
                    with (stage / asset()).open("rb") as source:
                        verify(source, fields)
                    with previous.open("rb") as old, target.open("rb") as current:
                        if fingerprint(old) != fingerprint(current):
                            return 1
                    (stage / "armed").touch()
                    go = stage / "go"
                    if not wait_for(lambda: go.exists() or cancelled(), 60) or not go.is_file():
                        return 1
                    state = json.loads(go.read_bytes())
                    while parent_alive(pid):
                        time.sleep(0.1)
                    swap(target, previous, stage, state, version)
                    return 0
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                time.sleep(0.5)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def snapshot(app):
    return dict(active=app.active, headless=app.headless, window=app.root.state(), argv=sys.argv[1:])


class Monitor:
    def __init__(self, target, resumed):
        self.target = target
        self.next_check = time.monotonic() + INTERVAL if resumed else 0
        self.executor = ThreadPoolExecutor(1)
        self.future = self.job = None
        self.cancel = threading.Event()

    def tick(self, app):
        enabled = app.config.last_good.get("auto_update", True)
        if not enabled:
            self.cancel.set()
            self.next_check = 0
        if self.future and self.future.done():
            try:
                self.job = self.future.result()
            except Exception:
                pass  # Falha de rede, armazenamento ou validação: manter o app.
            self.future = None
        if self.job:
            stage, process = self.job
            if process.poll() is not None:
                self.job = None
            elif self.cancel.is_set():
                self.abort(stage)
            elif not app.pending and not app.session_ip and (stage / "armed").is_file():
                try:
                    if not app.config.read().get("auto_update", True):
                        self.abort(stage)
                        return
                    write_json(stage / "go", snapshot(app))
                except (OSError, ValueError):
                    self.abort(stage)
                    return
                self.job = None
                app.close()
        elif enabled and not self.future and time.monotonic() >= self.next_check:
            self.cancel = threading.Event()
            self.next_check = time.monotonic() + INTERVAL
            self.future = self.executor.submit(prepare, self.target, self.cancel)

    @staticmethod
    def abort(stage):
        with suppress(OSError):
            (stage / "cancel").touch()

    def stop(self):
        self.cancel.set()
        if self.job:
            self.abort(self.job[0])
        self.executor.shutdown(wait=False)

def restore(app, state):
    if state["active"] and not app.config.last_good.get("review_required"):
        app.toggle_agent()
    window = state["window"]
    if window == "withdrawn":
        app.root.withdraw()
    elif window == "iconic":
        app.root.iconify()
    elif window == "zoomed":
        app.root.state("zoomed")

    def acknowledge():
        if app.done.is_set() or not state["version"]:
            return
        if app.pending or app.active != state["active"]:
            app.root.after(100, acknowledge)
            return
        if window == "withdrawn" and not app.headless:
            if not app.tray.ready.is_set():
                app.root.after(100, acknowledge)
                return
            app.root.withdraw()
        if state["version"] == __version__:
            write_bytes(Path(state["stage"]) / "ready", __version__.encode("ascii"))
            app.updated = True
            if not app.headless:
                app.render_update()
    app.root.after(100, acknowledge)
