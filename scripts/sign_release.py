"""Assina localmente o executável da tag que está em checkout."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from markcontrol import updater
from markcontrol.version import __version__


def generate(path):
    key = Ed25519PrivateKey.generate()
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as out:
        if os.name == "nt":
            import csv
            system = Path(os.environ["SystemRoot"]) / "System32"
            row = subprocess.check_output([system / "whoami.exe", "/user", "/fo", "csv", "/nh"])
            sid = next(csv.reader(row.decode("oem").splitlines()))[1]
            subprocess.run([system / "icacls.exe", path, "/inheritance:r", "/grant:r", f"*{sid}:F"],
                           check=True, stdout=subprocess.DEVNULL)
        out.write(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption()))
    print(key.public_key().public_bytes_raw().hex())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", nargs="?", type=Path)
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    path = args.generate or args.key
    if not path or path.resolve().is_relative_to(ROOT):
        parser.error("Informe um arquivo de chave fora do repositório.")
    if args.generate:
        if args.exe or args.key:
            parser.error("Use --generate sozinho.")
        generate(path.resolve())
        return
    if not args.exe or args.exe.name not in (updater.WINDOWS, updater.LINUX):
        parser.error("Use o executável com o nome do anexo da release.")
    key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    if key.public_key().public_bytes_raw().hex() != updater.PUBLIC_KEY_HEX:
        parser.error("A chave não corresponde à pública embutida.")
    with args.exe.open("rb") as stream:
        digest, size = updater.fingerprint(stream)
    if not size:
        parser.error("Arquivo vazio.")
    fields = dict(version=__version__, name=args.exe.name, sha256=digest, size=size)
    fields["signature"] = key.sign(updater.canonical(fields)).hex()
    args.exe.with_name(args.exe.name + ".sig").write_bytes(updater.canonical(fields))


if __name__ == "__main__":
    main()
