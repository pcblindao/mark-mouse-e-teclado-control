import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from markcontrol import history, wol
from markcontrol.client import Client
from markcontrol.config import Config
from markcontrol.inputctl import Input
from markcontrol.filetransfer import BLOCK, MAX_FILE, Receiver
from markcontrol.security import COMBOS, decode_message
from markcontrol.server import Agent, Control


def decode(kind, **fields):
    return decode_message(json.dumps(dict(t=kind, **fields)))


@pytest.mark.parametrize("name", ["", "../x", "a..b", "x/y", r"C:\x", "a:b", "x.", "x ",
                                  "CON .txt", "nul", "LPT¹.log", "conout$", "a\0", "\ud800"])
def test_bad_filename(name):
    assert decode("file_begin", name=name, size=0) is None


def test_wire_limits():
    assert decode("file_begin", name="ação.pdf", size=MAX_FILE)
    for size in (-1, MAX_FILE + 1, True, 1.0):
        assert decode("file_begin", name="x", size=size) is None
    assert decode("file_end", sha256="0" * 64)
    assert not decode("file_end", sha256="A" * 64)
    assert not decode("file_cancel", extra=1)
    assert decode("view_only", enabled=False) and not decode("view_only", enabled=0)
    for combo in COMBOS:
        assert decode("combo", keys=combo)
    assert not decode("combo", keys=["Ctrl", "Alt", "Del"])
    assert decode("chat", s="á" * 500) and not decode("chat", s="á" * 501)


def test_file_integrity_and_collision(tmp_path):
    data = b"x" * BLOCK + b"y"
    original = tmp_path / "doc.txt"
    original.write_bytes(b"original")
    incoming = Receiver(original.name, len(data), tmp_path)
    try:
        with pytest.raises(ValueError):
            incoming.block(data)
        incoming.block(data[:-1])
        with pytest.raises(ValueError):
            incoming.finish("0" * 64)
        incoming.block(data[-1:])
        with pytest.raises(ValueError):
            incoming.finish("0" * 64)
        incoming.finish(hashlib.sha256(data).hexdigest())
    finally:
        incoming.close()
    assert original.read_bytes() == b"original"
    assert (tmp_path / "doc (1).txt").read_bytes() == data
    assert not list(tmp_path.glob("*.part"))


def session():
    control = Control(Mock(alive=lambda: True), AsyncMock(), Mock(), 1)
    agent = Agent.__new__(Agent)
    agent.control = control
    agent.input = Mock(apply=AsyncMock())
    agent.allow_clipboard = agent.allow_files = True
    agent.notify = Mock()
    return agent, control


def test_read_only_and_chat():
    async def run():
        agent, c = session()
        c.region = {}
        await agent.handle(c, decode("view_only", enabled=True))
        agent.input.release_all.assert_called_once()
        for kind in ("move", "scroll", "click", "key", "text", "combo", "clip_set"):
            await agent.handle(c, {"t": kind})
        agent.input.apply.assert_not_awaited()
        await agent.handle(c, decode("view_only", enabled=False))
        await agent.handle(c, decode("combo", keys=["Win"]))
        agent.input.apply.assert_awaited_once()
        await agent.handle(c, decode("chat", s="Oi"))
        kind, event = agent.notify.call_args.args
        assert (kind, event["text"]) == ("chat", "Oi")
        with pytest.raises(ValueError):
            event["reply"]("x" * 501)
        await asyncio.wrap_future(event["reply"]("Olá"))
        c.ws.send_json.assert_awaited_with({"t": "chat", "s": "Olá"})
        count = c.ws.send_json.await_count
        await asyncio.wrap_future(event["reply"]("Duplicada"))
        assert c.ws.send_json.await_count == count
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["ok", "cancel", "disabled", "empty"])
def test_upload_client_agent(tmp_path, monkeypatch, mode):
    import markcontrol.server as server

    source = tmp_path / "origem.txt"
    source.write_bytes(b"" if mode == "empty" else b"x" * (BLOCK + 1))
    dest = tmp_path / "recebidos"
    monkeypatch.setattr(server, "Receiver", lambda name, size: Receiver(name, size, dest))

    async def run():
        agent, c = session()
        agent.allow_files = mode != "disabled"
        replies = asyncio.Queue(1)
        c.ws.send_json.side_effect = lambda message: replies.put_nowait(message["t"])

        async def send(message):
            await agent.handle(c, decode_message(json.dumps(message)))

        async def binary(data):
            await agent.file(c, "file_block", data)

        client = Client(None)
        client.files = True
        progress = []
        def on_progress(count, total):
            progress.append((count, total))
            if mode == "cancel":
                client.cancel_file()

        client.send_file(source, on_progress)
        ws = SimpleNamespace(send_json=send, send_bytes=binary)
        if mode == "disabled":
            with pytest.raises(ValueError):
                await client._send_upload(ws, client._upload, replies)
            assert not dest.exists()
            return
        status = await client._send_upload(ws, client._upload, replies)
        if mode == "cancel":
            assert status == "file_cancelled" and list(dest.iterdir()) == []
            assert progress == [(BLOCK, BLOCK + 1)]
        else:
            assert status == "file_ok"
            assert (dest / source.name).read_bytes() == source.read_bytes()
        assert c.upload is None
    asyncio.run(run())


def test_failed_block_discards_partial():
    async def run():
        agent, control = session()
        partial = Mock()
        partial.block.side_effect = ValueError("Bloco inválido.")
        control.upload = partial
        await agent.file(control, "file_block", b"x")
        partial.close.assert_called_once()
        assert control.upload is None
        control.ws.send_json.assert_awaited_with({"t": "file_error"})
    asyncio.run(run())


def test_combo_releases_keys_on_failure():
    device = Input.__new__(Input)
    device.keys = {"Control": "ctrl", "Shift": "shift", "Escape": "esc"}
    device.held_keys, device.held_buttons = set(), set()
    device.mouse, device.keyboard = Mock(), Mock()
    device.keyboard.press.side_effect = [None, None, OSError()]
    with pytest.raises(OSError):
        asyncio.run(device.apply(decode("combo", keys=["Ctrl", "Shift", "Esc"]), None, lambda: True))
    assert {call.args[0] for call in device.keyboard.release.call_args_list} == {"ctrl", "shift"}
    assert not device.held_keys


def test_file_setting_and_history(tmp_path):
    cfg = Config(tmp_path)
    old = cfg.defaults()
    del old["files"]
    cfg.path.write_text(json.dumps(old))
    assert cfg.read()["files"] is True
    cfg.save("PC", True, 65, files=False)
    cfg.save("PC", True, 70)
    assert cfg.read()["files"] is False
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{}] * 500))
    history.record(path, datetime.now(timezone.utc), "10.0.0.1", 2.7, {"chat", "files"})
    entries = json.loads(path.read_text())
    assert len(entries) == 500
    assert entries[-1].keys() == {"started", "duration_s", "ip", "features"}
    assert entries[-1]["duration_s"] == 2 and entries[-1]["features"] == ["chat", "files"]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_wol(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(wol, "interfaces", lambda: [("10.2.0.2", "255.255.255.0"),
                        ("192.168.1.2", None), ("8.8.8.8", "255.255.255.0")])
    sock = MagicMock()
    monkeypatch.setattr(wol.socket, "socket", lambda *_: sock)
    mac = "02:01:02:03:04:05"
    for target in ("8.8.8.255", "10.2.0.1", "255.255.255.255", "192.168.1.255"):
        with pytest.raises(ValueError):
            wol.wake(mac, target)
    with pytest.raises(ValueError):
        wol.wake("ff:ff:ff:ff:ff:ff", "10.2.0.255")
    wol.wake(mac, "10.2.0.255")
    udp = sock.__enter__.return_value
    udp.bind.assert_called_once_with(("10.2.0.2", 0))
    udp.sendto.assert_called_once_with(b"\xff" * 6 + bytes.fromhex("020102030405") * 16,
                                       ("10.2.0.255", 9))
