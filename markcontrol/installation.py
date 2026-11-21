"""Per-user installation and authenticated, single-instance desktop handoff."""
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue

from .certs import data_dir
from .config import read_json, write_json
from .version import __version__

CANONICAL_NAME = "MarkMouseTecladoControl.exe"
RESUME = "MARKCONTROL_UPDATE_STATE"
LEGACY = {
    "07145922ca50b1f0ffc35ba1e58b1483d8325892290de17a395c0dc5574b0108": ("0.5.1", 26960017),
    "fb17e27e884d2fa22b96fbc8330833cc37b1704e165e3284920073ca8bae9dc8": ("0.6.0", 26930327),
}
_instance = None


def canonical_path():
    return Path(os.environ.get("LOCALAPPDATA", data_dir().parent)) / "Programs" / "MarkControl" / CANONICAL_NAME


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def version(value):
    parts = value.split(".") if isinstance(value, str) else []
    if len(parts) != 3 or not all(part.isdigit() and len(part) <= 8 for part in parts):
        raise ValueError("Versão de instalação inválida")
    return tuple(map(int, parts))


class FileLock:
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = self.path.open("a+b")
        file.seek(0, 2)
        if not file.tell():
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            return False
        self.file = file
        return True

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


def wait_for(predicate, timeout=30):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)
    return True


class Instance:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = FileLock(self.folder / "desktop-instance.lock")
        self.events = Queue(32)
        self.stopped = threading.Event()
        self.ready = False
        self.nonce = secrets.token_hex(32)
        self.listener = None

    def acquire(self):
        if not self.lock.acquire():
            return False
        try:
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(8)
            self.listener.settimeout(0.2)
            write_json(self.folder / "desktop-instance.json", {
                "pid": os.getpid(), "port": self.listener.getsockname()[1],
                "nonce": self.nonce, "version": __version__, "executable": str(Path(sys.executable).resolve())})
            threading.Thread(target=self.serve, name="desktop-instance", daemon=True).start()
            return True
        except Exception:
            self.close()
            raise

    def serve(self):
        listener = self.listener
        while not self.stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                try:
                    raw = b""
                    while b"\n" not in raw and len(raw) <= 1024:
                        chunk = connection.recv(1025 - len(raw))
                        if not chunk:
                            break
                        raw += chunk
                    message = json.loads(raw) if len(raw) <= 1024 else None
                    accepted = (isinstance(message, dict) and set(message) == {"nonce", "action"}
                                and isinstance(message["nonce"], str)
                                and hmac.compare_digest(message["nonce"], self.nonce)
                                and message["action"] in ("open", "shutdown", "status"))
                    if accepted and message["action"] != "status":
                        self.events.put_nowait(message["action"])
                    connection.sendall(json.dumps({"accepted": bool(accepted), "ready": self.ready}).encode() + b"\n")
                except (OSError, ValueError, TypeError, Full):
                    pass

    def close(self):
        self.stopped.set()
        if self.listener:
            self.listener.close()
            self.listener = None
        # Keep the lock file itself: deleting it creates split lock identities.
        self.lock.close()


def notify(folder, action):
    try:
        state = read_json(Path(folder) / "desktop-instance.json", {})
        if (type(state.get("port")) is not int or not 1 <= state["port"] <= 65535
                or not isinstance(state.get("nonce"), str) or len(state["nonce"]) != 64):
            return None
        with socket.create_connection(("127.0.0.1", state["port"]), timeout=1) as connection:
            connection.sendall(json.dumps({"nonce": state["nonce"], "action": action}).encode() + b"\n")
            raw = b""
            while b"\n" not in raw and len(raw) <= 1024:
                chunk = connection.recv(1025 - len(raw))
                if not chunk:
                    break
                raw += chunk
            response = json.loads(raw)
            return response if response.get("accepted") is True else None
    except (OSError, ValueError, TypeError):
        return None


def actions():
    if _instance is not None:
        while True:
            try:
                yield _instance.events.get_nowait()
            except Empty:
                return


def mark_ready():
    if _instance is not None:
        _instance.ready = True


def installed_info(target):
    if not target.exists():
        return None
    checksum = digest(target)
    try:
        metadata = read_json(target.parent / "installation.json", {})
        version(metadata.get("version"))
        if metadata.get("sha256") == checksum:
            return metadata
    except (OSError, ValueError, TypeError):
        pass
    if checksum in LEGACY:
        return {"version": LEGACY[checksum][0], "sha256": checksum}
    raise ValueError("O arquivo existente na pasta do aplicativo não pôde ser identificado")


def mark_installed(target, release_version=__version__):
    write_json(target.parent / "installation.json", {"version": release_version, "sha256": digest(target)})


def launch(target, argv, *, handoff=False):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    env.pop(RESUME, None)
    env.pop("MARKCONTROL_INSTALL_HANDOFF", None)
    if handoff:
        env["MARKCONTROL_INSTALL_HANDOFF"] = "1"
    return subprocess.Popen([str(target), *argv], env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name("." + target.name + ".install-" + secrets.token_hex(8))
    try:
        shutil.copy2(source, staged)
        if digest(staged) != digest(source):
            raise OSError("Cópia incompleta do aplicativo")
        def replace():
            try:
                os.replace(staged, target)
                return True
            except PermissionError:
                return False
        if not wait_for(replace, 20):
            raise PermissionError("O aplicativo anterior ainda mantém o arquivo aberto")
    finally:
        staged.unlink(missing_ok=True)


def _legacy_processes():
    """Return held handles for same-user processes with official old binary hashes."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t), ("module", wintypes.DWORD),
                    ("threads", wintypes.DWORD), ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    def user(handle):
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(wintypes.HANDLE(handle), 8, ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                return None
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            length = advapi.GetLengthSid(ctypes.c_void_p(sid))
            return ctypes.string_at(sid, length)
        finally:
            kernel.CloseHandle(token)

    own_user = user(kernel.GetCurrentProcess())
    found, cache = [], {}
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return found
    try:
        entry = Entry(dwSize=ctypes.sizeof(Entry))
        valid = kernel.Process32FirstW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
        while valid:
            if entry.pid != os.getpid():
                handle = kernel.OpenProcess(0x1000 | 0x100000 | 1, False, entry.pid)
                can_stop = bool(handle)
                if not handle:
                    handle = kernel.OpenProcess(0x1000 | 0x100000, False, entry.pid)
                keep = False
                if handle:
                    try:
                        path_buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                        if (own_user is not None and user(handle) == own_user
                                and kernel.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, path_buffer, ctypes.byref(size))):
                            path = Path(path_buffer.value)
                            helper = path.name.lower() == "previous.exe" and path.parent.name.startswith(".") and path.parent.name.endswith(".updates")
                            if not helper and path.resolve() != Path(sys.executable).resolve() and path.stat().st_size in {v[1] for v in LEGACY.values()}:
                                key = str(path).lower()
                                if key not in cache:
                                    cache[key] = digest(path)
                                checksum = cache[key]
                                if checksum in LEGACY:
                                    found.append((handle, path, entry.pid, entry.parent, can_stop))
                                    keep = True
                    except OSError:
                        pass
                    finally:
                        if not keep:
                            kernel.CloseHandle(wintypes.HANDLE(handle))
            valid = kernel.Process32NextW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    finally:
        kernel.CloseHandle(wintypes.HANDLE(snapshot))
    parent_ids = {item[3] for item in found}
    return [(handle, path, can_stop) for handle, path, _, _, can_stop in sorted(found, key=lambda item: item[2] in parent_ids)]


def stop_legacy():
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handles = _legacy_processes()
    aliases = []
    try:
        if any(not can_stop for _, _, can_stop in handles):
            raise PermissionError("Uma cópia anterior requer permissão para encerrar")
        for handle, path, _ in handles:
            # Identity is checked while holding this exact process handle; no PID/name kill.
            if kernel.WaitForSingleObject(wintypes.HANDLE(handle), 100) != 0 and not kernel.TerminateProcess(wintypes.HANDLE(handle), 0) and kernel.WaitForSingleObject(wintypes.HANDLE(handle), 0) != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel.WaitForSingleObject(wintypes.HANDLE(handle), 10000) != 0:
                raise TimeoutError("O aplicativo anterior não encerrou")
            aliases.append(path)
    finally:
        for handle, _, _ in handles:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    return list(dict.fromkeys(aliases))


@dataclass
class Startup:
    exit_code: int | None = None
    instance: Instance | None = None

    def close(self):
        global _instance
        if self.instance:
            self.instance.close()
            _instance = None


def stop_owner(folder):
    probe = FileLock(Path(folder) / "desktop-instance.lock")
    if probe.acquire():
        probe.close()
        return
    if notify(folder, "shutdown") is None:
        raise RuntimeError("A cópia em execução não respondeu à instalação")
    if not wait_for(probe.acquire):
        raise TimeoutError("O aplicativo anterior não encerrou")
    probe.close()


def migrate_aliases(source, aliases):
    for alias in aliases:
        if alias != source and alias.exists() and digest(alias) in LEGACY:
            atomic_copy(source, alias)


def bootstrap(argv=None):
    global _instance
    argv = list(sys.argv[1:] if argv is None else argv)
    if (not getattr(sys, "frozen", False) or os.name != "nt" or any(arg in ("-h", "--help") for arg in argv)
            or (argv and argv[0] not in ("--autostart", "agent"))):
        return Startup()
    folder = data_dir()
    source, target = Path(sys.executable).resolve(), canonical_path().resolve()
    background = "--autostart" in argv or bool(argv and argv[0] == "agent")
    handoff = os.environ.pop("MARKCONTROL_INSTALL_HANDOFF", None) == "1"
    # The old updater requires this exact child to remain alive until its health
    # acknowledgement. Relocate only on a later normal launch.
    relocate = source != target and not os.environ.get(RESUME)
    if relocate:
        install_lock = FileLock(folder / "desktop-install.lock")
        if not wait_for(install_lock.acquire):
            raise TimeoutError("Outra instalação está em andamento")
        try:
            owner_status = notify(folder, "status")
            if owner_status is not None:
                owner_state = read_json(folder / "desktop-instance.json", {})
                if version(owner_state.get("version")) >= version(__version__):
                    if not background:
                        notify(folder, "open")
                    return Startup(exit_code=0)
            existing = installed_info(target)
            if existing and version(existing["version"]) >= version(__version__):
                status = notify(folder, "status")
                if status is not None:
                    state = read_json(folder / "desktop-instance.json", {})
                    if version(state.get("version")) >= version(existing["version"]):
                        if not background:
                            notify(folder, "open")
                        return Startup(exit_code=0)
                stop_owner(folder)
                migrate_aliases(target, stop_legacy())
                launch(target, argv, handoff=True)
                return Startup(exit_code=0)
            stop_owner(folder)
            aliases = stop_legacy()
            backup = target.with_name(".bootstrap-previous.exe")
            if target.exists():
                shutil.copy2(target, backup)
            atomic_copy(source, target)
            mark_installed(target)
            process = launch(target, argv, handoff=True)
            healthy = wait_for(lambda: process.poll() is not None or bool((notify(folder, "status") or {}).get("ready")))
            if not healthy or process.poll() is not None:
                if process.poll() is None:
                    notify(folder, "shutdown")
                    if not wait_for(lambda: process.poll() is not None, 10):
                        raise TimeoutError("A nova cópia não confirmou a inicialização")
                if backup.exists():
                    atomic_copy(backup, target)
                    if existing:
                        mark_installed(target, existing["version"])
                    launch(target, argv, handoff=True)
                raise RuntimeError("A nova cópia não confirmou a inicialização")
            migrate_aliases(target, aliases)
            return Startup(exit_code=0)
        finally:
            install_lock.close()
    if source == target and not handoff and not os.environ.get(RESUME):
        probe = FileLock(folder / "desktop-install.lock")
        if not probe.acquire():
            return Startup(exit_code=0)
        probe.close()
    instance = Instance(folder)
    if not instance.acquire():
        contacted = wait_for(lambda: notify(folder, "status" if background else "open") is not None, 3)
        return Startup(exit_code=0 if contacted else 1)
    _instance = instance
    try:
        if source == target:
            mark_installed(target)
            from .gui import autostart
            try:
                if autostart.enabled():
                    autostart.set_enabled(True)
            except OSError as exc:
                from .diagnostics import event
                event("autostart_failed", exc)
    except Exception:
        instance.close()
        _instance = None
        raise
    return Startup(instance=instance)
