import asyncio
import threading
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from queue import Empty

from PIL import Image

from markcontrol import capture, client


def test_wss_loopback_keeps_commands_flowing_during_blocked_decode(tmp_path, monkeypatch):
    """Real TLS/WebSocket/client/server, with synthetic capture and no OS input."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from markcontrol import certs, server
    from markcontrol.config import Config
    from markcontrol.discovery import Peer

    entered, release = threading.Event(), threading.Event()
    original_decode = client.prepare_frame

    def decode(*args):
        entered.set()
        assert release.wait(5)
        return original_decode(*args)

    regions = [{"left": 0, "top": 0, "width": 640, "height": 480},
               {"left": 640, "top": 0, "width": 800, "height": 600}]
    payloads = {}
    for monitor, region in enumerate(regions, 1):
        for height in (0, 300):
            image = Image.new("RGB", capture.output_size(region["width"], region["height"], height), "#123456")
            buffer = BytesIO()
            image.save(buffer, "JPEG", quality=90, subsampling=0)
            payloads[monitor, height] = buffer.getvalue()

    capture_calls = []

    class SyntheticCapture:
        def __init__(self):
            self.index = 0

        def frame(self, monitor, quality, previous, force, height=0):
            self.index += 1
            capture_calls.append((monitor, quality, height))
            return monitor, 2, regions[monitor - 1], self.index, payloads[monitor, height]

        def close(self):
            pass

    monkeypatch.setenv("MARKCONTROL_DEV_LOOPBACK", "1")
    monkeypatch.setattr(certs, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(client, "Trust", lambda: SimpleNamespace(key=lambda *_: None, verified=lambda *_: None))
    monkeypatch.setattr(client, "prepare_frame", decode)
    monkeypatch.setattr(capture, "StreamCapture", SyntheticCapture)
    monkeypatch.setattr(server.wol, "local_mac", lambda: None)
    capture_events = []
    monkeypatch.setattr(server, "event", lambda *args, **kwargs: capture_events.append(args))
    monkeypatch.setattr(client, "event", lambda *args, **kwargs: None)

    async def run():
        applied = asyncio.Queue()

        class Device:
            async def apply(self, message, region, active):
                applied.put_nowait((message, region))

            def release_all(self):
                pass

        monkeypatch.setattr(server, "Input", Device)
        cfg = Config(tmp_path)
        agent = server.Agent(True, 2, cfg, lambda *_: None)
        agent.quality = 85
        context, fingerprint, _ = certs.tls_identity("127.0.0.1", [])
        app = web.Application(middlewares=[server.local_https])
        async def index(request):
            return web.Response(text="test")
        app.router.add_get("/", index)
        app.router.add_post("/api/login", agent.login)
        app.router.add_get("/ws", agent.websocket)
        listener = TestServer(app, host="127.0.0.1", scheme="https")
        await listener.start_server(ssl=context)
        c = client.Client(Peer("127.0.0.1", "Synthetic", listener.port, fingerprint, False, False))
        c.target = (320, 240)
        connection = asyncio.create_task(c.connect(None, False))

        async def consume_control():
            while True:
                try:
                    c.events.get_nowait()
                except Empty:
                    await asyncio.sleep(0.001)

        consumer = asyncio.create_task(consume_control())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            c.send({"t": "stream", "q": 90, "fps": 60, "monitor": 2, "height": 300})
            # Stream geometry is changed by the server while the old monitor's
            # decoder remains occupied, then input uses the original monitor.
            for _ in range(100):
                if agent.control and agent.control.region == regions[1]:
                    break
                await asyncio.sleep(0.02)
            assert agent.control.region == regions[1], (capture_events, capture_calls[-5:])
            c.send({"t": "click", "b": "left", "d": "down", "x": 1, "y": 0})
            message, region = await asyncio.wait_for(applied.get(), 0.5)
            assert message["t"] == "click" and region == regions[1]
            assert not release.is_set()
            assert c.frames.empty()
            release.set()
            for _ in range(100):
                if not c.frames.empty():
                    break
                await asyncio.sleep(0.02)
            assert c.frames.get_nowait().size == (320, 240)
            assert agent.control.fps == 60 and agent.control.max_height == 300
        finally:
            release.set()
            c.close()
            connection.cancel()
            consumer.cancel()
            await asyncio.gather(connection, consumer, return_exceptions=True)
            await listener.close()

    asyncio.run(run())


def test_slow_decode_does_not_block_mouse_and_keyboard(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    image = Image.new("RGB", (4, 4))

    def decode(*_):
        entered.set()
        assert release.wait(3)
        return image

    monkeypatch.setattr(client, "prepare_frame", decode)

    async def run():
        c = client.Client(None)
        ready, sent = asyncio.Event(), asyncio.Event()
        ready.set()
        messages = []

        async def send(message):
            messages.append(message)
            sent.set()

        tasks = [asyncio.create_task(c.decode_stream([(0, b"jpeg")], ready)),
                 asyncio.create_task(c.write_stream(SimpleNamespace(send_json=send)))]
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            c.send({"t": "key", "k": "a", "d": "down"})
            await asyncio.wait_for(sent.wait(), 0.5)
            assert not release.is_set()
            assert messages == [{"t": "key", "k": "a", "d": "down"}]
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_frames_replace_stale_frames_without_blocking_control_events():
    async def run():
        c = client.Client(None)
        c.events.put(("message", "first"))
        c.events.put(("message", "second"))
        for value in range(100):
            await asyncio.wait_for(c.emit("frame", value), 0.2)
        assert c.frames.qsize() == 1
        assert c.frames.get_nowait() == 99
        assert c.events.get_nowait() == ("message", "first")
        assert c.events.get_nowait() == ("message", "second")
    asyncio.run(run())


def test_capture_reuses_device_and_preserves_original_mouse_coordinates(monkeypatch):
    region = {"left": 1920, "top": 0, "width": 800, "height": 600}
    shot = SimpleNamespace(size=(800, 600), bgra=b"\x40\x50\x60\xff" * (800 * 600))
    screen = SimpleNamespace(monitors=[{}, region], grab=Mock(return_value=shot), close=Mock())
    factory = Mock(return_value=screen)
    monkeypatch.setattr(capture.mss, "mss", factory)
    source = capture.StreamCapture()
    try:
        first = source.frame(1, 90, None, True, 300)
        second = source.frame(1, 90, first[3], False, 300)
        assert first[2] == region
        assert second[4] is None
        with Image.open(BytesIO(first[4])) as image:
            assert image.size == (400, 300)
        native = source.frame(1, 90, first[3], False, 0)
        with Image.open(BytesIO(native[4])) as image:
            assert image.size == (800, 600)
        factory.assert_called_once()
        assert screen.grab.call_count == 3
    finally:
        source.close()
    screen.close.assert_called_once()


def test_jpeg_draft_respects_target_and_native_dimensions():
    source = Image.new("RGB", (3840, 2160), "#123456")
    buffer = BytesIO()
    source.save(buffer, "JPEG", quality=95, subsampling=0)
    data = buffer.getvalue()
    assert client.prepare_frame(data, (1600, 900)).size == (1600, 900)
    assert client.prepare_frame(data, None).size == (3840, 2160)
    assert capture.output_size(3840, 2160, 1440) == (2560, 1440)
    assert capture.output_size(1366, 768, 2160) == (1366, 768)
