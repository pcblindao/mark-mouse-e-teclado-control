import hmac
import ipaddress
import json
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

from .filetransfer import valid_name as valid_filename, valid_size
from .clipboard import ClipboardError, check_text

NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "::1/128", "fe80::/10",
)))
TTL = 30 * 60
FAILURE_WINDOW = 5 * 60
BLOCK_TIME = 10 * 60
MAX_MESSAGE = 620_000  # Acomoda 100 KiB mesmo com escapes JSON de seis bytes.
NAMED_KEYS = {
    "Alt": "alt", "AltGraph": "alt_gr", "Backspace": "backspace",
    "CapsLock": "caps_lock", "Control": "ctrl", "Delete": "delete",
    "ArrowDown": "down", "End": "end", "Enter": "enter", "Escape": "esc",
    "Home": "home", "Insert": "insert", "ArrowLeft": "left", "Meta": "cmd",
    "PageDown": "page_down", "PageUp": "page_up", "ArrowRight": "right",
    "Shift": "shift", "Tab": "tab", "ArrowUp": "up", " ": "space",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}
KEYS = frozenset(NAMED_KEYS) | frozenset(chr(i) for i in range(33, 127))


def private_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in NETWORKS)


@dataclass
class Session:
    token: str
    peer: str
    deadline: float
    clock: Callable[[], float]
    stopped: Event = field(default_factory=Event)

    def alive(self):
        return not self.stopped.is_set() and self.clock() < self.deadline

    def renew(self):
        if self.alive():
            self.deadline = self.clock() + TTL


class Authorization:
    def __init__(self, authenticate, clock=time.monotonic):
        self.clock = clock
        self.authenticate = authenticate
        self.pending = None
        self.failures = {}
        self.blocks = {}

    def login(self, peer, body):
        now = self.clock()
        for address, until in list(self.blocks.items()):
            if until <= now:
                del self.blocks[address]
        for address, attempts in list(self.failures.items()):
            while attempts and attempts[0] <= now - FAILURE_WINDOW:
                attempts.popleft()
            if not attempts:
                del self.failures[address]

        if peer in self.blocks:
            return 429, None
        extra = self.authenticate(body)
        if extra is None:
            attempts = self.failures.setdefault(peer, deque())
            attempts.append(now)
            if len(attempts) >= 5:
                self.blocks[peer] = now + BLOCK_TIME
                del self.failures[peer]
                return 429, None
            return 401, None

        self.pending = Session(secrets.token_hex(32), peer, now + TTL, self.clock)
        return 200, {"token": self.pending.token, **extra}

    def claim(self, peer, token):
        session = self.pending
        if (session and session.alive() and peer == session.peer
                and isinstance(token, str) and re.fullmatch(r"[0-9a-f]{64}", token)
                and hmac.compare_digest(token, session.token)):
            self.pending = None
            return session
        return None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _coordinates(message):
    return all(type(message[k]) in (int, float) and 0 <= message[k] <= 1
               for k in ("x", "y"))


def _text(value):
    if not isinstance(value, str) or len(value) > 500 or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _clipboard(value):
    try:
        check_text(value)
    except ClipboardError:
        return False
    return True


def valid_chat(value):
    return _text(value) and bool(value.strip())


COMBOS = (["Win"], ["Win", "D"], ["Win", "L"], ["Alt", "Tab"], ["Ctrl", "Shift", "Esc"],
          ["PrintScreen"], ["Alt", "F4"])


RULES = {
    "file_begin": ("name size", lambda m: valid_filename(m["name"]) and valid_size(m["size"])),
    "file_end": ("sha256", lambda m: isinstance(m["sha256"], str)
                 and re.fullmatch("[a-f0-9]{64}", m["sha256"])),
    "file_cancel": ("", lambda m: True),
    "view_only": ("enabled", lambda m: type(m["enabled"]) is bool),
    "combo": ("keys", lambda m: m["keys"] in COMBOS),
    "chat": ("s", lambda m: valid_chat(m["s"])),
    "move": ("x y", _coordinates),
    "click": ("b d x y", lambda m: _coordinates(m)
              and m["b"] in ("left", "right", "middle") and m["d"] in ("down", "up")),
    "scroll": ("dx dy", lambda m: all(_integer(m[k], -20, 20) for k in ("dx", "dy"))),
    "key": ("k d", lambda m: isinstance(m["k"], str)
            and m["k"] in KEYS and m["d"] in ("down", "up")),
    "text": ("s", lambda m: _text(m["s"])),
    "quality": ("q fps monitor", lambda m: _integer(m["q"], 30, 90)
                and _integer(m["fps"], 1, 20) and _integer(m["monitor"], 1, 256)),
    "ping": ("n", lambda m: _integer(m["n"], 0, 2**31 - 1)),
    "clip_set": ("s", lambda m: _clipboard(m["s"])),
    "clip_get": ("", lambda m: True),
}


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Campo repetido.")
    return result


def decode_message(raw):
    if not isinstance(raw, str) or len(raw) > MAX_MESSAGE:
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_MESSAGE:
            return None
        message = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, RecursionError):
        return None
    if not isinstance(message, dict) or not isinstance(message.get("t"), str):
        return None
    rule = RULES.get(message["t"])
    if rule is None:
        return None
    fields, validate = rule
    if message.keys() != {"t", *fields.split()} or not validate(message):
        return None
    return message
