import asyncio

import pytest

from markcontrol import clipboard


def test_limit_in_utf8_bytes():
    assert clipboard.check_text("") == 0
    assert clipboard.check_text("a" * clipboard.MAX_BYTES) == clipboard.MAX_BYTES
    assert clipboard.check_text("é" * (clipboard.MAX_BYTES // 2)) == clipboard.MAX_BYTES
    with pytest.raises(clipboard.ClipboardError):
        clipboard.check_text("🙂" * (clipboard.MAX_BYTES // 4 + 1))


@pytest.mark.parametrize("value", [b"texto", None, 1, "\ud800"])
def test_only_utf8_text(value):
    with pytest.raises(clipboard.ClipboardError):
        clipboard.check_text(value)


def test_clipboard_roundtrip(monkeypatch):
    local = []
    monkeypatch.setattr(clipboard.pyperclip, "copy", local.append)
    monkeypatch.setattr(clipboard.pyperclip, "paste", lambda: local[-1])
    assert asyncio.run(clipboard.write_text("Olá", lambda: True)) == 4
    assert asyncio.run(clipboard.read_text(lambda: True)) == ("Olá", 4)


def test_inactive_session_does_not_access_clipboard(monkeypatch):
    def forbidden(*args):
        pytest.fail("Clipboard chamado sem sessão ativa.")

    monkeypatch.setattr(clipboard.pyperclip, "copy", forbidden)
    monkeypatch.setattr(clipboard.pyperclip, "paste", forbidden)
    with pytest.raises(clipboard.ClipboardError):
        asyncio.run(clipboard.write_text("Olá", lambda: False))
    with pytest.raises(clipboard.ClipboardError):
        asyncio.run(clipboard.read_text(lambda: False))


def test_reject_oversize_local_clipboard(monkeypatch):
    monkeypatch.setattr(clipboard.pyperclip, "paste", lambda: "a" * (clipboard.MAX_BYTES + 1))
    with pytest.raises(clipboard.ClipboardError):
        asyncio.run(clipboard.read_text(lambda: True))
