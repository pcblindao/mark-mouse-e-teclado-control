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


def valid_size(size):
    return type(size) is int and 0 <= size <= MAX_FILE


class Receiver:
    def __init__(self, name, size, directory=None):
        if not valid_name(name) or not valid_size(size):
            raise ValueError("Cabeçalho inválido.")
        self.directory = directory or Path.home() / "Downloads" / "Mark Mouse e Teclado Control"
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
                return
            except FileExistsError:
                continue
        raise ValueError("Limite de nomes repetidos.")

    def close(self):
        try:
            self.temp.close()
        finally:
            Path(self.temp.name).unlink(missing_ok=True)
