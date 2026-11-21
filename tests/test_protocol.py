import json

import pytest

from markcontrol.clipboard import MAX_BYTES
from markcontrol.security import MAX_MESSAGE, decode_message


@pytest.mark.parametrize("message", [
    {"t": "move", "x": 0, "y": 1},
    {"t": "click", "x": 0.5, "y": 0, "b": "middle", "d": "up"},
    {"t": "scroll", "dx": -20, "dy": 20},
    {"t": "key", "k": "ArrowDown", "d": "down"},
    {"t": "key", "k": "?", "d": "up"},
    {"t": "text", "s": "é" * 500},
    {"t": "quality", "q": 30, "fps": 1, "monitor": 1},
    {"t": "quality", "q": 95, "fps": 20, "monitor": 256},
    {"t": "quality", "q": 95, "fps": 60, "monitor": 1},
    {"t": "stream", "q": 90, "fps": 60, "monitor": 1, "height": 2160},
    {"t": "ping", "n": 2**31 - 1},
    {"t": "clip_set", "s": "a" * MAX_BYTES},
    {"t": "clip_set", "s": "\x01" * MAX_BYTES},
    {"t": "clip_get"},
])
def test_valid(message):
    assert decode_message(json.dumps(message)) == message


@pytest.mark.parametrize("message", [
    None, [], {}, {"t": []}, {"t": "ping", "n": 1, "extra": 1},
    {"t": "move", "x": float("nan"), "y": 1},
    {"t": "move", "x": -0.01, "y": 1},
    {"t": "move", "x": 0, "y": 1.01},
    {"t": "click", "x": 0, "y": 0, "b": "back", "d": "down"},
    {"t": "click", "x": 0, "y": 0, "b": "left", "d": "tap"},
    {"t": "scroll", "dx": -21, "dy": 0},
    {"t": "scroll", "dx": 0, "dy": 1.0},
    {"t": "key", "k": "Unidentified", "d": "down"},
    {"t": "key", "k": "a", "d": "repeat"},
    {"t": "text", "s": "x" * 501},
    {"t": "text", "s": "\ud800"},
    {"t": "quality", "q": 29, "fps": 20, "monitor": 1},
    {"t": "quality", "q": 96, "fps": 20, "monitor": 1},
    {"t": "quality", "q": 90, "fps": 0, "monitor": 1},
    {"t": "quality", "q": 90, "fps": 61, "monitor": 1},
    {"t": "stream", "q": 90, "fps": 60, "monitor": 1, "height": True},
    {"t": "stream", "q": 90, "fps": 60, "monitor": 1, "height": 4321},
    {"t": "quality", "q": 90, "fps": 20, "monitor": 0},
    {"t": "quality", "q": 90, "fps": 20, "monitor": 257},
    {"t": "ping", "n": True},
    {"t": "ping", "n": 2**31},
    {"t": "clip_set", "s": "é" * (MAX_BYTES // 2 + 1)},
    {"t": "clip_set", "s": ["texto"]},
    {"t": "clip_get", "s": ""},
])
def test_invalid(message):
    assert decode_message(json.dumps(message)) is None


def test_frame_limit():
    ping = '{"t":"ping","n":1}'
    padded = ping.ljust(MAX_MESSAGE)
    assert decode_message(padded) == {"t": "ping", "n": 1}
    assert decode_message(padded + " ") is None
    assert decode_message(b'{"t":"ping","n":1}') is None
