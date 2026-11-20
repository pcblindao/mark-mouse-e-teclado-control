"""Opt-in technical diagnostics. No GitHub credentials live in the client."""
import hashlib
import json
import platform
import threading
import time
import uuid
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .certs import data_dir
from .config import read_json, write_json
from .version import __version__

# Set only after deploying and verifying the receiver over HTTPS.
ENDPOINT = "https://mktcommarcos.com.br/api/mark-mouse-teclado/diagnostics"
CONSENT_VERSION = 1
from .diagnostic_schema import EVENTS, ERRORS, clean_record
_lock = threading.RLock()


def preference(directory=None):
    try:
        value = read_json((directory or data_dir()) / "diagnostic-consent.json", {})
        if (isinstance(value, dict) and value.get("version") == CONSENT_VERSION
                and type(value.get("enabled")) is bool):
            return value["enabled"]
    except (OSError, ValueError, RecursionError):
        pass
    return None


def set_preference(enabled, directory=None):
    if type(enabled) is not bool:
        raise ValueError("Escolha inválida de diagnóstico")
    directory = directory or data_dir()
    with _lock:
        write_json(directory / "diagnostic-consent.json",
                   {"version": CONSENT_VERSION, "enabled": enabled})
        if not enabled:
            (directory / "diagnostic-pending.json").unlink(missing_ok=True)


def pending(directory):
    try:
        value = read_json(directory / "diagnostic-pending.json", [])
        return value[-50:] if isinstance(value, list) else []
    except (OSError, ValueError, RecursionError):
        return []


def enqueue(record, directory=None):
    directory = directory or data_dir()
    try:
        with _lock:
            if preference(directory) is not True:
                return
            record = clean_record(record)
            if record is None:
                return
            rows = pending(directory)
            rows.append({"id": uuid.uuid4().hex, "time": int(time.time()), "record": record})
            write_json(directory / "diagnostic-pending.json", rows[-50:])
    except (OSError, ValueError, RecursionError):
        pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transmit(payload, endpoint):
    if not endpoint.startswith("https://"):
        raise ValueError("O coletor exige HTTPS")
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        result = json.loads(response.read(1024))
        if response.status not in (200, 202) or result != {"accepted": payload["report_id"]}:
            raise ValueError("Diagnóstico não confirmado pelo coletor")


def send_pending(directory=None, endpoint=None, transport=transmit):
    directory = directory or data_dir()
    endpoint = ENDPOINT if endpoint is None else endpoint
    with _lock:
        if not endpoint or preference(directory) is not True:
            return False
        rows = pending(directory)
        if not any(row["record"]["event"] in ERRORS for row in rows):
            return False
        ids = {row["id"] for row in rows}
        payload = {"schema": 1, "report_id": hashlib.sha256("".join(sorted(ids)).encode()).hexdigest(),
                   "version": __version__, "os": platform.system(),
                   "os_release": platform.release()[:64], "python": platform.python_version(),
                   "events": [{"time": row["time"], **row["record"]} for row in rows]}
    transport(payload, endpoint)
    with _lock:
        if preference(directory) is True:
            remaining = [row for row in pending(directory) if row["id"] not in ids]
            write_json(directory / "diagnostic-pending.json", remaining)
    return True


class Sender:
    def __init__(self, directory=None):
        self.directory = directory or data_dir()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="diagnostics", daemon=True)
        self.thread.start()

    def run(self):
        delay = 30
        while not self.stopped.wait(delay):
            try:
                send_pending(self.directory)
                delay = 300
            except Exception:
                # Offline failures never affect remote control; keep bounded
                # pending events and retry with a capped backoff.
                delay = min(3600, delay * 2)

    def stop(self):
        self.stopped.set()
