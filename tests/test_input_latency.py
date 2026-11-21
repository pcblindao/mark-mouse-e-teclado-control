import asyncio
import hashlib
import json
import threading
import time
from io import BytesIO
from queue import Empty
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from markcontrol import capture, certs, client, server
from markcontrol.config import Config
from markcontrol.security import Authorization
from markcontrol.filetransfer import BLOCK, Receiver


def test_outgoing_motion_keeps_barriers_and_never_replaces_releases():
    c = client.Client(None)
    for index in range(200):
        c.send({"t": "move", "x": index / 199, "y": 0})
    c.send({"t": "click", "b": "left", "d": "down", "x": 1, "y": 0})
    for index in range(200):
        c.send({"t": "move", "x": 1 - index / 199, "y": 0})
    c.send({"t": "click", "b": "left", "d": "up", "x": 0, "y": 0})
    c.send({"t": "key", "k": "a", "d": "down"})
    c.send({"t": "key", "k": "a", "d": "up"})
    messages = []
    while not c.outgoing.empty():
        messages.append(c.outgoing.get_nowait())
    assert [m["t"] for m in messages] == ["move", "click", "move", "click", "key", "key"]
    assert messages[0]["x"] == 1 and messages[2]["x"] == 0
    assert [m["d"] for m in messages if m["t"] != "move"] == ["down", "up", "down", "up"]
    assert c.outgoing.dropped_moves == 398


def test_nonmotion_overload_closes_safely_instead_of_unbounded_growth(monkeypatch):
    monkeypatch.setattr(client, "event", lambda *_: None)
    c = client.Client(None)
    for _ in range(513):
        c.send({"t": "key", "k": "a", "d": "up"})
    assert c.closed.is_set() and c.outgoing.qsize() == 512


def test_motion_rate_is_capped_but_release_interrupts_the_motion_wait():
    async def run():
        c = client.Client(None)
        sent, released = [], asyncio.Event()

        async def send(message):
            sent.append((time.perf_counter(), message))
            if message.get("d") == "up":
                released.set()

        task = asyncio.create_task(c.write_stream(SimpleNamespace(send_json=send)))
        stopped = threading.Event()

        def producer():
            start = time.perf_counter()
            index = 0
            while not stopped.is_set() and time.perf_counter() - start < 0.3:
                c.send({"t": "move", "x": (index % 100) / 100, "y": 0})
                index += 1
                time.sleep(0.001)

        producer_thread = threading.Thread(target=producer)
        producer_thread.start()
        try:
            await asyncio.to_thread(producer_thread.join)
            c.send({"t": "key", "k": "Control", "d": "up"})
            await asyncio.wait_for(released.wait(), 0.2)
            motions = [stamp for stamp, message in sent if message["t"] == "move"]
            assert len(motions) <= 21  # 60 Hz plus first/final command-barrier flush.
            assert len(motions) >= 2
            assert sent[-1][1] == {"t": "key", "k": "Control", "d": "up"}
        finally:
            stopped.set()
            producer_thread.join()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def benchmark_input_burst(tmp_path, monkeypatch, asynchronous=False, legacy=False):
    """Raw legacy controller sends every motion; real TLS/WSS preserves barriers."""
    handled, metrics, measurements = [], [], {}
    completed = None

    class Input:
        def release_all(self):
            pass

        async def apply(self, message, region, active):
            if asynchronous:
                await asyncio.to_thread(time.sleep, 0.02)
            else:
                time.sleep(0.02)
            handled.append(message)
            if message["t"] == "key" and message["d"] == "up":
                completed.set()

    image = BytesIO()
    Image.new("RGB", (32, 24), "#123456").save(image, "JPEG")
    jpeg = image.getvalue()

    class Screen:
        def frame(self, monitor, quality, previous, force, height=0):
            return 1, 1, {"left": 0, "top": 0, "width": 32, "height": 24}, 1, jpeg if force else None

        def close(self):
            pass

    monkeypatch.setenv("MARKCONTROL_DEV_LOOPBACK", "1")
    monkeypatch.setattr(certs, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "Input", Input)
    monkeypatch.setattr(capture, "StreamCapture", Screen)
    monkeypatch.setattr(server.wol, "local_mac", lambda: None)
    monkeypatch.setattr(server, "event", lambda name, *args, **kwargs: metrics.append((name, kwargs)))

    async def run():
        nonlocal completed
        completed = asyncio.Event()
        agent = server.Agent(True, 1, Config(tmp_path), lambda *_: None)
        agent.quality = 85
        if legacy:
            class SerializedReader:
                def __init__(self, control):
                    self.control = control

                async def put(self, message):
                    await agent.handle(self.control, message)

                async def get(self):
                    await asyncio.Future()
            monkeypatch.setattr(server, "InputQueue", SerializedReader)
        context, fingerprint, _ = certs.tls_identity("127.0.0.1", [])
        app = web.Application(middlewares=[server.local_https])
        app.router.add_post("/api/login", agent.login)
        app.router.add_get("/ws", agent.websocket)
        listener = TestServer(app, host="127.0.0.1", scheme="https")
        await listener.start_server(ssl=context)
        pin = aiohttp.Fingerprint(bytes.fromhex(fingerprint.replace(":", "")))
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(f"https://127.0.0.1:{listener.port}/api/login", json={}, ssl=pin) as response:
                    token = (await response.json())["token"]
                async with http.ws_connect(f"wss://127.0.0.1:{listener.port}/ws", ssl=pin,
                                           protocols=("markcontrol", token)) as ws:
                    while True:
                        packet = await ws.receive()
                        if packet.type == aiohttp.WSMsgType.TEXT and json.loads(packet.data).get("t") == "info":
                            break

                    async def consume():
                        async for packet in ws:
                            pass

                    reader = asyncio.create_task(consume())
                    started = time.perf_counter()
                    for index in range(200):
                        await ws.send_json({"t": "move", "x": index / 199, "y": 0})
                    await ws.send_json({"t": "click", "b": "left", "d": "down", "x": 1, "y": 0})
                    for index in range(200):
                        await ws.send_json({"t": "move", "x": 1 - index / 199, "y": 0})
                    await ws.send_json({"t": "click", "b": "left", "d": "up", "x": 0, "y": 0})
                    await ws.send_json({"t": "key", "k": "a", "d": "down"})
                    await ws.send_json({"t": "key", "k": "a", "d": "up"})
                    try:
                        await asyncio.wait_for(completed.wait(), 15 if legacy else 1)
                        elapsed = time.perf_counter() - started
                        assert elapsed < 15 if legacy else elapsed < 0.75
                        moves = [m for m in handled if m["t"] == "move"]
                        commands = [m for m in handled if m["t"] != "move"]
                        assert len(moves) == 400 if legacy else len(moves) <= 8
                        assert [(m["t"], m["d"]) for m in commands] == [
                            ("click", "down"), ("click", "up"), ("key", "down"), ("key", "up")]
                        first_click = handled.index(commands[0])
                        second_click = handled.index(commands[1])
                        assert handled[first_click - 1]["x"] == 1
                        assert handled[second_click - 1]["x"] == 0
                        print(f"slow_input={('async' if asynchronous else 'sync')} handled_moves={len(moves)} elapsed_ms={elapsed * 1000:.1f}")
                        measurements.update(mode="legacy" if legacy else "coalesced", asynchronous=asynchronous,
                                            input_ms=20, received_moves=400, handled_moves=len(moves),
                                            commands=len(commands), elapsed_ms=round(elapsed * 1000, 1))
                    finally:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
        finally:
            await listener.close()
    asyncio.run(run())
    return measurements


@pytest.mark.parametrize("asynchronous", (False, True))
def test_legacy_wss_burst_coalesces_before_slow_native_input(tmp_path, monkeypatch, asynchronous):
    benchmark_input_burst(tmp_path, monkeypatch, asynchronous)


def test_wss_file_header_and_binary_pipeline_preserve_order(tmp_path, monkeypatch):
    """Header need not be executed before the socket reader sees its blocks."""
    monkeypatch.setenv("MARKCONTROL_DEV_LOOPBACK", "1")
    monkeypatch.setattr(certs, "data_dir", lambda: tmp_path / "identity")
    monkeypatch.setattr(server, "Receiver", lambda name, size: Receiver(name, size, tmp_path / "received"))
    monkeypatch.setattr(server, "event", lambda *args, **kwargs: None)

    async def run():
        agent = server.Agent.__new__(server.Agent)
        agent.auth = Authorization(lambda body: {} if body == {} else None)
        agent.control = None
        agent.monitors, agent.quality = 1, 85
        agent.allow_files = agent.allow_clipboard = True
        agent.input = SimpleNamespace(release_all=lambda: None)
        agent.history_path = tmp_path / "history.json"
        agent.notify = lambda *_: None

        async def no_screen(control):
            await asyncio.Event().wait()
        agent.stream = no_screen
        context, fingerprint, _ = certs.tls_identity("127.0.0.1", [])
        app = web.Application(middlewares=[server.local_https])
        app.router.add_post("/api/login", agent.login)
        app.router.add_get("/ws", agent.websocket)
        listener = TestServer(app, host="127.0.0.1", scheme="https")
        await listener.start_server(ssl=context)
        pin = aiohttp.Fingerprint(bytes.fromhex(fingerprint.replace(":", "")))
        data = b"x" * BLOCK + b"lastblock"
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(f"https://127.0.0.1:{listener.port}/api/login", json={}, ssl=pin) as response:
                    token = (await response.json())["token"]
                async with http.ws_connect(f"wss://127.0.0.1:{listener.port}/ws", ssl=pin,
                                           protocols=("markcontrol", token)) as ws:
                    await ws.send_json({"t": "file_begin", "name": "payload.bin", "size": len(data)})
                    await ws.send_bytes(data[:BLOCK])
                    await ws.send_bytes(data[BLOCK:])
                    await ws.send_json({"t": "file_end", "sha256": hashlib.sha256(data).hexdigest()})
                    replies = []
                    while "file_ok" not in replies:
                        packet = await asyncio.wait_for(ws.receive(), 2)
                        assert packet.type == aiohttp.WSMsgType.TEXT
                        replies.append(json.loads(packet.data)["t"])
                    assert replies == ["file_ready", "file_ready", "file_ready", "file_ok"]
                    assert (tmp_path / "received" / "payload.bin").read_bytes() == data
        finally:
            await listener.close()
    asyncio.run(run())
