import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from markcontrol import inputctl
from markcontrol.gui.viewer import Viewer, protocol_key
from markcontrol.inputctl import Input
from markcontrol.security import KEYS

VIEWER_JS = Path(inputctl.__file__).parent / "static" / "viewer.js"


def device():
    dev = Input.__new__(Input)
    dev.keyboard = Mock()
    dev.mouse = Mock()
    dev.keys = {"Enter": "ENTER", "Tab": "TAB"}
    dev.held_keys, dev.held_buttons = set(), set()
    return dev


def tk_ui():
    messages = []
    ui = SimpleNamespace(photo=True, view_only=False, clip=False, keys={}, buttons=set(),
                         client=SimpleNamespace(send=messages.append))
    return ui, messages


# 1. Repetição automática ---------------------------------------------------------------

def test_input_repeated_down_presses_again_and_single_up_releases():
    dev = device()
    for _ in range(3):
        asyncio.run(dev.apply({"t": "key", "k": "a", "d": "down"}, None, lambda: True))
    assert dev.keyboard.press.call_count == 3 and dev.held_keys == {"a"}
    asyncio.run(dev.apply({"t": "key", "k": "a", "d": "up"}, None, lambda: True))
    dev.keyboard.release.assert_called_once_with("a")
    assert dev.held_keys == set()
    asyncio.run(dev.apply({"t": "key", "k": "a", "d": "up"}, None, lambda: True))
    dev.keyboard.release.assert_called_once()


def test_tk_held_key_repeats_down_then_single_up():
    ui, messages = tk_ui()
    event = SimpleNamespace(keycode=65, keysym="a", char="a", state=0)
    for _ in range(3):
        Viewer.key(ui, event, True)
    Viewer.key(ui, event, False)
    assert messages == [dict(t="key", k="a", d="down")] * 3 + [dict(t="key", k="a", d="up")]
    assert ui.keys == {}


def test_tk_held_non_ascii_char_repeats_text():
    ui, messages = tk_ui()
    event = SimpleNamespace(keycode=186, keysym="ccedilla", char="ç", state=0)
    Viewer.key(ui, event, True)
    Viewer.key(ui, event, True)
    Viewer.key(ui, event, False)
    assert messages == [dict(t="text", s="ç")] * 2


def test_browser_forwards_repeat_and_non_ascii_text():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "if (event.repeat) send({t: \"key\", k: keys.get(code), d: \"down\"});" in js
    assert "send({t: \"text\", s: key});" in js


# 2. Ctrl+pontuação e teclado numérico no Tk -------------------------------------------

@pytest.mark.parametrize("sym,char,key", [
    ("minus", "\x1f", "-"), ("equal", "", "="), ("slash", "", "/"), ("comma", "", ","),
    ("period", "", "."), ("semicolon", "", ";"), ("apostrophe", "", "'"),
    ("bracketleft", "\x1b", "["), ("bracketright", "\x1d", "]"), ("backslash", "\x1c", "\\"),
    ("grave", "", "`"), ("plus", "", "+"), ("KP_Add", "", "+"), ("KP_Subtract", "", "-"),
    ("KP_Multiply", "", "*"), ("KP_Divide", "", "/"), ("KP_Decimal", "", "."),
    ("KP_0", "", "0"), ("KP_7", "", "7"), ("KP_Home", "", "Home"), ("KP_End", "", "End"),
    ("KP_Prior", "", "PageUp"), ("KP_Next", "", "PageDown"), ("KP_Up", "", "ArrowUp"),
    ("KP_Down", "", "ArrowDown"), ("KP_Left", "", "ArrowLeft"), ("KP_Right", "", "ArrowRight"),
    ("KP_Insert", "", "Insert"), ("KP_Delete", "", "Delete"), ("minus", "-", "-"),
])
def test_tk_ctrl_punctuation_and_keypad(sym, char, key):
    assert protocol_key(sym, char) == key
    assert key in KEYS


def test_tk_ctrl_minus_is_forwarded():
    ui, messages = tk_ui()
    event = SimpleNamespace(keycode=189, keysym="minus", char="\x1f", state=4)
    Viewer.key(ui, event, True)
    Viewer.key(ui, event, False)
    assert messages == [dict(t="key", k="-", d=d) for d in ("down", "up")]


# 4/5. Rolagem ------------------------------------------------------------------------

def wheel_ui():
    ui, messages = tk_ui()
    ui.pointer = lambda e, kind: True
    return ui, messages


def scrolls(messages):
    return [(m["dx"], m["dy"]) for m in messages if m["t"] == "scroll"]


def test_tk_wheel_accumulates_delta_and_keeps_remainder(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    ui, messages = wheel_ui()
    plain = SimpleNamespace(state=0)
    for _ in range(3):
        Viewer.wheel(ui, plain, delta=40)
    assert scrolls(messages) == [(0, 1)]
    Viewer.wheel(ui, plain, delta=-240)
    assert scrolls(messages) == [(0, 1), (0, -2)]
    Viewer.wheel(ui, plain, delta=120 * 25)
    assert scrolls(messages)[2:] == [(0, 20), (0, 5)]


def test_tk_shift_wheel_up_scrolls_left():
    ui, messages = wheel_ui()
    shifted = SimpleNamespace(state=1)
    Viewer.wheel(ui, shifted, delta=120)
    Viewer.wheel(ui, shifted, 1)  # X11 Button-4 com Shift
    Viewer.wheel(ui, shifted, delta=-120)
    assert scrolls(messages) == [(-1, 0), (-1, 0), (1, 0)]


def test_tk_x11_buttons():
    ui, messages = wheel_ui()
    plain = SimpleNamespace(state=0)
    Viewer.wheel(ui, plain, 1)
    Viewer.wheel(ui, plain, -1)
    Viewer.wheel(ui, plain, -1, horizontal=True)  # Button-6
    Viewer.wheel(ui, plain, 1, horizontal=True)   # Button-7
    assert scrolls(messages) == [(0, 1), (0, -1), (-1, 0), (1, 0)]


def test_browser_wheel_does_not_invert_dx():
    js = VIEWER_JS.read_text(encoding="utf-8")
    assert "-Math.sign(delta)" not in js
    assert 'let dx = notches("x", event.deltaX), dy = -notches("y", event.deltaY);' in js


# 6. Texto via Unicode ----------------------------------------------------------------

def test_text_uses_unicode_on_windows(monkeypatch):
    monkeypatch.setattr(inputctl.sys, "platform", "win32")
    sent = []
    monkeypatch.setattr(inputctl, "send_unicode", sent.append)
    dev = device()
    asyncio.run(dev.apply({"t": "text", "s": "aB\nç\t😀"}, None, lambda: True))
    assert sent == ["a", "B", "ç", "😀"]
    assert [c.args[0] for c in dev.keyboard.tap.call_args_list] == ["ENTER", "TAB"]
    dev.keyboard.type.assert_not_called()


def test_text_stops_when_inactive(monkeypatch):
    monkeypatch.setattr(inputctl.sys, "platform", "win32")
    sent = []
    monkeypatch.setattr(inputctl, "send_unicode", sent.append)
    calls = iter([True, False])
    asyncio.run(device().apply({"t": "text", "s": "abc"}, None, lambda: next(calls)))
    assert sent == ["a"]


def test_text_uses_pynput_elsewhere(monkeypatch):
    monkeypatch.setattr(inputctl.sys, "platform", "linux")
    monkeypatch.setattr(inputctl, "send_unicode", Mock(side_effect=AssertionError))
    dev = device()
    asyncio.run(dev.apply({"t": "text", "s": "ab"}, None, lambda: True))
    assert [c.args[0] for c in dev.keyboard.type.call_args_list] == ["a", "b"]


def test_unicode_units_surrogate_pairs():
    assert inputctl.unicode_units("ç") == [0xE7]
    assert inputctl.unicode_units("😀") == [0xD83D, 0xDE00]


@pytest.mark.skipif(not hasattr(__import__("ctypes"), "windll"), reason="só Windows")
def test_send_unicode_builds_keyeventf_unicode_inputs(monkeypatch):
    import ctypes
    captured = {}

    def fake_send(count, array, size):
        captured.update(count=count, size=size,
                        events=[(array[i].type, array[i].u.ki.wVk, array[i].u.ki.wScan,
                                 array[i].u.ki.dwFlags) for i in range(count)])
        return count

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendInput=fake_send)))
    inputctl.send_unicode("😀")
    assert captured["size"] == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert captured["events"] == [(1, 0, 0xD83D, 4), (1, 0, 0xD83D, 6),
                                  (1, 0, 0xDE00, 4), (1, 0, 0xDE00, 6)]
    monkeypatch.setattr(ctypes, "windll",
                        SimpleNamespace(user32=SimpleNamespace(SendInput=lambda *a: 0)))
    with pytest.raises(OSError):
        inputctl.send_unicode("a")
