"""Histórico local das sessões concluídas."""
from .config import lock, read_json, write_json


def record(path, started, peer, seconds, features):
    entry = dict(started=started.isoformat(), ip=peer, duration_s=max(0, int(seconds)),
                 features=sorted(features))
    with lock(path):
        entries = read_json(path, [])
        if not isinstance(entries, list):
            raise ValueError("Histórico inválido.")
        write_json(path, entries[-499:] + [entry])
