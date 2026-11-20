"""Preferências e credenciais do agente no perfil do usuário."""
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .certs import data_dir


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    write_bytes(path, json.dumps(value, ensure_ascii=True).encode("ascii"))


def write_bytes(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        name = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            temp.close()
            os.replace(name, path)
        finally:
            temp.close()
            name.unlink(missing_ok=True)


@contextmanager
def lock(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def valid_name(name):
    return isinstance(name, str) and 1 <= len(name) <= 255 and name.isprintable() and bool(name.strip())


def pin_hash(pin, salt=None):
    if not isinstance(pin, str) or not re.fullmatch("[0-9]{8}", pin):
        raise ValueError("Informe oito dígitos para o PIN.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(pin.encode("ascii"), salt=bytes.fromhex(salt),
                            n=2**14, r=8, p=1, dklen=32)
    return salt + ":" + digest.hex()


class Config:
    def __init__(self, directory=None):
        self.path = (directory or data_dir()) / "config.json"
        self.last_good = None
        self.stamp = object()
        self.had_file = False

    @staticmethod
    def defaults():
        return dict(name=socket.gethostname(), clipboard=True, files=True, quality=65, pin=None, devices=[])

    def read(self):
        cfg = read_json(self.path, None if self.had_file else self.defaults())
        if (not isinstance(cfg, dict) or not valid_name(cfg.get("name"))
                or type(cfg.get("files", True)) is not bool
                or type(cfg.get("clipboard")) is not bool or type(cfg.get("quality")) is not int
                or not 30 <= cfg["quality"] <= 90 or "pin" not in cfg
                or (cfg["pin"] is not None and (not isinstance(cfg["pin"], str)
                    or not re.fullmatch("[0-9a-f]{32}:[0-9a-f]{64}", cfg["pin"])))
                or not isinstance(cfg.get("devices"), list)
                or not all(isinstance(d, str) and re.fullmatch("[0-9a-f]{64}", d) for d in cfg["devices"])):
            raise ValueError("Configuração inválida.")
        cfg.setdefault("files", True)
        return cfg

    def refresh(self, warn, *, opening=False):
        # Cada instância pertence a uma única thread. Só avançar o marcador
        # da leitura observada, para detectar substituições durante a leitura.
        try:
            stat = self.path.stat()
            stamp = stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            stamp = None
        if stamp == self.stamp:
            return self.last_good
        try:
            if stamp is not None:
                self.had_file = True
            self.last_good = self.read()
        except (ValueError, RecursionError):
            if self.last_good is None:
                if not opening:
                    raise ValueError("Configuração inválida. Abra a janela para revisar.")
                with lock(self.path):
                    try:
                        self.last_good = self.read()
                    except (ValueError, RecursionError):
                        write_bytes(self.path.with_suffix(".json.bad"), self.path.read_bytes())
                        self.last_good = dict(self.defaults(), review_required=True)
                        write_json(self.path, self.last_good)
            else:
                warn("Configuração inválida. Mantidos os últimos ajustes válidos.")
        self.stamp = stamp
        return self.last_good

    def save(self, name, clipboard, quality, pin=None, *, files=None):
        # None mantém o PIN atual. String vazia desliga o PIN.
        if not valid_name(name) or type(clipboard) is not bool or type(quality) is not int or not 30 <= quality <= 90:
            raise ValueError("Verifique nome e qualidade (30 a 90).")
        with lock(self.path):
            cfg = self.read()
            if files is not None:
                if type(files) is not bool:
                    raise ValueError("Opção de arquivos inválida.")
                cfg["files"] = files
            cfg.pop("review_required", None)
            cfg.update(name=name, clipboard=clipboard, quality=quality)
            if pin is not None:
                cfg.update(pin=pin_hash(pin) if pin else None, devices=[])
            write_json(self.path, cfg)

    def forget(self):
        with lock(self.path):
            cfg = self.read()
            cfg["devices"] = []
            write_json(self.path, cfg)

    def authenticate(self, body):
        """None indica recusa. O dicionário de sucesso pode incluir a chave emitida."""
        with lock(self.path):
            try:
                cfg = self.read()
            except (ValueError, RecursionError):
                return None
            if cfg.get("review_required"):
                return None
            if cfg["pin"] is None:
                return {} if body == {} else None
            if not isinstance(body, dict):
                return None
            if body.keys() == {"device_key"}:
                key = body["device_key"]
                if isinstance(key, str) and re.fullmatch("[0-9a-f]{64}", key):
                    digest = hashlib.sha256(bytes.fromhex(key)).hexdigest()
                    if any(hmac.compare_digest(digest, known) for known in cfg["devices"]):
                        return {}
                return None
            if body.keys() not in ({"pin"}, {"pin", "remember"}) or type(body.get("remember", False)) is not bool:
                return None
            try:
                candidate = pin_hash(body["pin"], cfg["pin"].split(":")[0])
            except ValueError:
                return None
            if not hmac.compare_digest(candidate, cfg["pin"]):
                return None
            if not body.get("remember"):
                return {}
            if len(cfg["devices"]) >= 256:
                raise ValueError("Limite de aparelhos. Use Esquecer aparelhos.")
            key = secrets.token_bytes(32)
            cfg["devices"].append(hashlib.sha256(key).hexdigest())
            write_json(self.path, cfg)
            return {"device_key": key.hex()}
