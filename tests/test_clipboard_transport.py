import asyncio
import json
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from io import BytesIO
from queue import Empty
from types import SimpleNamespace
from unittest.mock import Mock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image
from multidict import CIMultiDict

from markcontrol import capture, certs, client, clipboard, server
from markcontrol.clipboard_bundle import create_bundle
from markcontrol.config import Config
from markcontrol.discovery import Peer
from markcontrol.inputctl import Input


async def wait_until(predicate, seconds=3):
    deadline = asyncio.get_running_loop().time() + seconds
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for clipboard operation")
        await asyncio.sleep(0.02)


async def clipboard_event(c, expected="clipboard_ok"):
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        try:
            kind, value = c.events.get_nowait()
        except Empty:
            await asyncio.sleep(0.02)
            continue
        if kind in ("clipboard_ok", "clipboard_error"):
            assert kind == expected, value
            return value
    raise AssertionError("No clipboard completion event")


@asynccontextmanager
async def connected(tmp_path, monkeypatch):
    state = {"client": clipboard.ClipboardPayload("text", "local"),
             "host": clipboard.ClipboardPayload("text", "remote"), "sequence": 0, "writes": []}

    def native(side):
        async def read(active):
            if not active():
                raise clipboard.ClipboardError("unavailable")
            return state[side]

        async def write(payload, active):
            if not active():
                raise clipboard.ClipboardError("unavailable")
            clipboard.check_payload(payload)
            state[side] = payload
            state["writes"].append((side, payload.kind))
        return SimpleNamespace(read_payload=read, write_payload=write,
                               sequence_number=lambda: state["sequence"], ClipboardError=clipboard.ClipboardError)

    buffer = BytesIO()
    Image.new("RGB", (64, 48), "#123456").save(buffer, "JPEG")
    jpeg = buffer.getvalue()

    class Screen:
        def frame(self, monitor, quality, previous, force, height=0):
            region = {"left": 0, "top": 0, "width": 64, "height": 48}
            return 1, 1, region, 1, jpeg if force or previous is None else None

        def close(self):
            pass

    device = Input.__new__(Input)
    device.keys = {"Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "meta"}
    device.held_keys, device.held_buttons = {"ctrl"}, set()
    device.keyboard, device.mouse = Mock(), Mock()
    device.keyboard_module = SimpleNamespace(Controller=SimpleNamespace(InvalidCharacterException=ValueError))
    monkeypatch.setenv("MARKCONTROL_DEV_LOOPBACK", "1")
    monkeypatch.setattr(certs, "data_dir", lambda: tmp_path / "tls")
    monkeypatch.setattr(client, "data_dir", lambda: tmp_path / "client-cache")
    monkeypatch.setattr(server, "data_dir", lambda: tmp_path / "host-cache")
    monkeypatch.setattr(client, "clipboard", native("client"))
    monkeypatch.setattr(server, "clipboard", native("host"))
    monkeypatch.setattr(client, "Trust", lambda: SimpleNamespace(key=lambda *_: None, verified=lambda *_: None))
    monkeypatch.setattr(capture, "StreamCapture", Screen)
    monkeypatch.setattr(server, "Input", lambda: device)
    monkeypatch.setattr(server.wol, "local_mac", lambda: None)
    monkeypatch.setattr(server, "event", lambda *a, **kw: None)
    monkeypatch.setattr(client, "event", lambda *a, **kw: None)
    agent = server.Agent(True, 1, Config(tmp_path / "config"), lambda *_: None)
    agent.quality = 85
    context, fingerprint, _ = certs.tls_identity("127.0.0.1", [])
    app = web.Application(middlewares=[server.local_https], client_max_size=1024)

    async def index(request):
        return web.Response(text="test")

    app.router.add_get("/", index)
    app.router.add_post("/api/login", agent.login)
    app.router.add_get("/ws", agent.websocket)
    app.router.add_get("/api/clipboard", agent.clipboard_http, allow_head=False)
    app.router.add_put("/api/clipboard", agent.clipboard_http)
    listener = TestServer(app, host="127.0.0.1", scheme="https")
    await listener.start_server(ssl=context)
    c = client.Client(Peer("127.0.0.1", "Synthetic", listener.port, fingerprint, False, False))
    c.target = (64, 48)
    task = asyncio.create_task(c.connect(None, False))
    try:
        await wait_until(lambda: c.clipboard_rich and c._clipboard_context is not None)
        yield SimpleNamespace(c=c, agent=agent, state=state, device=device,
                              url=f"https://127.0.0.1:{listener.port}/api/clipboard",
                              fingerprint=aiohttp.Fingerprint(bytes.fromhex(fingerprint.replace(":", ""))))
    finally:
        c.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await listener.close()


def payload(kind, tmp_path):
    if kind == "text":
        return clipboard.ClipboardPayload("text", "Texto com acentos: ação\nsegunda linha")
    if kind == "image":
        buffer = BytesIO()
        Image.new("RGBA", (32, 24), (30, 90, 120, 150)).save(buffer, "PNG")
        return clipboard.ClipboardPayload("image", buffer.getvalue())
    folder = tmp_path / "selected"
    folder.mkdir()
    (folder / "empty").mkdir()
    (folder / "document.bin").write_bytes(bytes(range(256)) * 1024)
    return clipboard.ClipboardPayload("files", (str(folder),))


def assert_payload_equal(expected, actual):
    assert actual.kind == expected.kind
    if expected.kind != "files":
        assert actual.value == expected.value
        return
    from pathlib import Path
    source, received = Path(expected.value[0]), Path(actual.value[0])
    assert source != received
    assert received.name == source.name
    assert (received / "document.bin").read_bytes() == (source / "document.bin").read_bytes()
    assert (received / "empty").is_dir()


@pytest.mark.parametrize("kind", ("text", "image", "files"))
def test_rich_clipboard_bidirectional_over_real_tls(tmp_path, monkeypatch, kind):
    async def run():
        async with connected(tmp_path, monkeypatch) as env:
            original = payload(kind, tmp_path)
            env.state["client"] = original
            assert env.c.paste_clipboard(paste_after=True)
            event = await clipboard_event(env.c)
            assert event == {"direction": "paste", "kind": kind}
            assert_payload_equal(original, env.state["host"])
            assert env.device.held_keys == {"ctrl"}
            assert env.c.copy_clipboard()
            event = await clipboard_event(env.c)
            assert event == {"direction": "copy", "kind": kind}
            assert_payload_equal(env.state["host"], env.state["client"])
            assert env.device.held_keys == {"ctrl"}
    asyncio.run(run())


def test_copy_first_preserves_physical_control_until_real_key_up(tmp_path, monkeypatch):
    async def run():
        async with connected(tmp_path, monkeypatch) as env:
            def pressed(key):
                if key == "c":
                    env.state["host"] = clipboard.ClipboardPayload("text", "new selected text")
                    env.state["sequence"] += 1
            env.device.keyboard.press.side_effect = pressed
            assert env.c.copy_clipboard(copy_first=True)
            await clipboard_event(env.c)
            assert env.state["client"].value == "new selected text"
            assert env.device.held_keys == {"ctrl"}
            env.c.send({"t": "key", "k": "Control", "d": "up"})
            await wait_until(lambda: not env.device.held_keys)
    asyncio.run(run())


def test_clipboard_endpoint_authorization_and_permissions(tmp_path, monkeypatch):
    async def run():
        async with connected(tmp_path, monkeypatch) as env, aiohttp.ClientSession() as http:
            token = env.agent.control.session.token
            headers = {"X-Mark-Session": token}
            for bad in ({}, {"X-Mark-Session": "0" * 64}, dict(headers, Origin="https://invalid.example")):
                async with http.get(env.url, ssl=env.fingerprint, headers=bad) as response:
                    assert response.status == 403
            class Request(dict):
                pass
            wrong_ip = Request(peer="127.0.0.2")
            wrong_ip.headers = CIMultiDict(headers)
            with pytest.raises(web.HTTPForbidden):
                env.agent.clipboard_control(wrong_ip)
            env.agent.control.view_only = True
            async with http.put(env.url, ssl=env.fingerprint, headers=dict(headers, **{"Content-Type": "application/octet-stream"}), data=b"x") as response:
                assert response.status == 403
            async with http.get(env.url + "?copy_first=1", ssl=env.fingerprint, headers=headers) as response:
                assert response.status == 403
            async with http.get(env.url, ssl=env.fingerprint, headers=headers) as response:
                assert response.status == 200
                await response.read()
            env.agent.control.view_only = False
            env.agent.allow_files = False
            env.state["host"] = payload("files", tmp_path)
            async with http.get(env.url, ssl=env.fingerprint, headers=headers) as response:
                assert response.status == 403
            archive = tmp_path / "forbidden.bundle"
            create_bundle(env.state["host"], archive, lambda: True)
            async with http.put(env.url, ssl=env.fingerprint,
                                headers=dict(headers, **{"Content-Type": "application/octet-stream"}),
                                data=archive.read_bytes()) as response:
                assert response.status == 403
            assert not (tmp_path / "host-cache" / "clipboard-received").exists()
            env.agent.allow_clipboard = False
            async with http.get(env.url, ssl=env.fingerprint, headers=headers) as response:
                assert response.status == 403
            assert not env.state["writes"]
    asyncio.run(run())


@pytest.mark.parametrize("direction", ("copy", "paste"))
def test_corrupt_bundle_never_changes_native_clipboard(tmp_path, monkeypatch, direction):
    def corrupt(value, path, active):
        create_bundle(value, path, active)
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest["sha256"] = "0" * 64
        members["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, value in members.items():
                archive.writestr(name, value)

    async def run():
        async with connected(tmp_path, monkeypatch) as env:
            monkeypatch.setattr(server if direction == "copy" else client, "create_bundle", corrupt)
            assert (env.c.copy_clipboard() if direction == "copy" else env.c.paste_clipboard())
            await clipboard_event(env.c, "clipboard_error")
            assert env.state["client"].value == "local"
            assert env.state["host"].value == "remote"
            assert not env.state["writes"]
    asyncio.run(run())


def test_clipboard_packaging_does_not_block_input_and_revoke_cancels_it(tmp_path, monkeypatch):
    entered = threading.Event()

    def packaging(value, path, active):
        entered.set()
        deadline = time.monotonic() + 3
        while active() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not active():
            raise clipboard.ClipboardError("cancelled")
        raise AssertionError("Clipboard operation did not observe revocation")

    async def run():
        async with connected(tmp_path, monkeypatch) as env:
            monkeypatch.setattr(client, "create_bundle", packaging)
            assert env.c.paste_clipboard()
            assert await asyncio.to_thread(entered.wait, 1)
            assert not env.c.copy_clipboard()
            env.c.send({"t": "key", "k": "x", "d": "down"})
            await wait_until(lambda: any(call.args == ("x",) for call in env.device.keyboard.press.call_args_list), 0.5)
            env.c.set_view_only(True)
            await clipboard_event(env.c, "clipboard_error")
            assert not env.c._clipboard_busy
            assert not env.state["writes"]
    asyncio.run(run())


def test_streamed_upload_limit_is_separate_from_global_login_limit(tmp_path, monkeypatch):
    async def run():
        async with connected(tmp_path, monkeypatch) as env, aiohttp.ClientSession() as http:
            monkeypatch.setattr(server, "MAX_BUNDLE_BYTES", 128)

            async def chunks():
                for _ in range(5):
                    yield b"x" * 32

            async with http.put(env.url, ssl=env.fingerprint, headers={
                    "X-Mark-Session": env.agent.control.session.token,
                    "Content-Type": "application/octet-stream"}, data=chunks()) as response:
                assert response.status == 413
            assert not env.state["writes"]
    asyncio.run(run())
