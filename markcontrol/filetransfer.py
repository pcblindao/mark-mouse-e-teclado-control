"""Recepção de um arquivo por sessão autenticada."""
import hashlib
import os
import re
import tempfile
from pathlib import Path

BLOCK = 256 * 1024
MAX_FILE = 2 << 30
RESERVED = re.compile(r"CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]", re.I)


def valid_name(name):
    return (isinstance(name, str) and name.isprintable() and 0 < len(name.encode("utf-8")) <= 240
            and ".." not in name and name[-1] not in ". "
            and not any(c in '<>:"/\\|?*' for c in name)
            and not RESERVED.fullmatch(name.partition(".")[0].rstrip(" ")))


def downloads_dir():
    """Pasta Downloads real do usuário (o Windows permite movê-la para outro disco ou OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid
            folder = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
            guid = (ctypes.c_byte * 16).from_buffer_copy(folder.bytes_le)
            path = ctypes.c_wchar_p()
            shell = ctypes.WinDLL("shell32")
            shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                   ctypes.POINTER(ctypes.c_wchar_p)]
            if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0:
                try:
                    if path.value:
                        return Path(path.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(path)
        except (OSError, AttributeError, ValueError):
            pass
    return Path.home() / "Downloads"


def valid_size(size):
    return type(size) is int and 0 <= size <= MAX_FILE


class Receiver:
    def __init__(self, name, size, directory=None):
        if not valid_name(name) or not valid_size(size):
            raise ValueError("Cabeçalho inválido.")
        self.directory = directory or downloads_dir() / "Mark Mouse e Teclado Control"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.resolve() != self.directory.parent.resolve() / self.directory.name:
            raise ValueError("Diretório redirecionado.")
        self.name = Path(name)
        self.remaining = size
        self.digest = hashlib.sha256()
        self.temp = tempfile.NamedTemporaryFile(dir=self.directory, suffix=".part", delete=False,
                                                buffering=0)

    def block(self, data):
        if not data or len(data) != min(BLOCK, self.remaining):
            raise ValueError("Bloco inválido.")
        if self.temp.write(data) != len(data):
            raise OSError("Gravação incompleta.")
        self.digest.update(data)
        self.remaining -= len(data)

    def finish(self, checksum):
        if self.remaining or self.digest.hexdigest() != checksum:
            raise ValueError("Arquivo incompleto ou inválido.")
        self.temp.close()
        for i in range(10000):
            name = self.name if not i else f"{self.name.stem} ({i}){self.name.suffix}"
            try:
                target = self.directory / name
                if os.name == "nt":
                    os.rename(self.temp.name, target)
                else:
                    os.link(self.temp.name, target)
                return target
            except FileExistsError:
                continue
        raise ValueError("Limite de nomes repetidos.")

    def close(self):
        try:
            self.temp.close()
        finally:
            Path(self.temp.name).unlink(missing_ok=True)
