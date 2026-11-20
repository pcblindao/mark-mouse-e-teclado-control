"""Bounded technical events; never record input, credentials or exception text."""
import json
import logging
from logging.handlers import RotatingFileHandler
import platform
import sys
import traceback
from pathlib import Path
from urllib.parse import urlencode

from .certs import data_dir
from .version import __version__

logger = logging.getLogger("markcontrol.diagnostics")
logger.propagate = False
logger.addHandler(logging.NullHandler())

ISSUES = "https://github.com/pcblindao/mark-mouse-e-teclado-control/issues/new"


def report(directory=None):
    folder = (directory or data_dir()) / "logs"
    lines = []
    # Read bounded tails only; include rotated history after a restart.
    for name in ("diagnostics.log.1", "diagnostics.log"):
        try:
            with (folder / name).open("rb") as source:
                source.seek(0, 2)
                size = source.tell()
                source.seek(max(0, size - 4096))
                tail = source.read(4096).decode("utf-8", errors="replace")
                lines.extend(tail.splitlines()[1:] if size > 4096 else tail.splitlines())
        except OSError:
            continue
    selected = []
    length = 0
    for line in reversed(lines):
        if length + len(line) + 1 > 1600:
            break
        selected.append(line)
        length += len(line) + 1
    details = "\n".join(reversed(selected)) or "Nenhum evento disponível."
    return (f"Versão: {__version__}\nSistema: {sys.platform} {platform.release()}\n"
            f"Python: {platform.python_version()}\n\nEventos técnicos recentes:\n{details}")


def issue_url(details):
    return ISSUES + "?" + urlencode({
        "title": f"Diagnóstico do aplicativo v{__version__}",
        "body": "Descreva o problema e como reproduzir:\n\n\nDiagnóstico técnico:\n" + details[:2000],
    })


def event(name, error=None):
    record = {"event": name, "version": __version__}
    if error is not None:
        record["error_type"] = type(error).__name__
        record["frames"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno,
             "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-12:]
        ]
    logger.info(json.dumps(record, ensure_ascii=True))


def setup(directory=None):
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return
    try:
        folder = (directory or data_dir()) / "logs"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler = RotatingFileHandler(folder / "diagnostics.log", maxBytes=256 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        event("startup")
        logger.info(json.dumps({"os": sys.platform, "os_release": platform.release(),
                                "python": platform.python_version()}))
    except OSError:
        # A read-only/full profile must not prevent starting the app.
        return
