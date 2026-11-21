import asyncio
import ctypes
import os
import struct
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from markcontrol import clipboard as c


def png():
    out = BytesIO()
    Image.new("RGBA", (40, 30), (20, 80, 140, 128)).save(out, "PNG")
    return out.getvalue()


class FakeWindows:
    def __init__(self, data=None, files=()):
        self.data = data or {}
        self.paths = files
        self.emptied = self.closed = False
        self.user = SimpleNamespace(GetClipboardSequenceNumber=lambda: 123)

    @contextmanager
    def opened(self, active, write=False):
        c._active(active)
        try:
            yield self
        finally:
            self.closed = True

    def register(self, name):
        return {"PNG": 50001, "Preferred DropEffect": 50002}[name]

    def has(self, kind):
        return kind in self.data

    def read(self, kind, limit):
        assert len(self.data[kind]) <= limit
        return self.data[kind]

    def empty(self):
        self.data.clear()
        self.emptied = True

    def put(self, kind, value):
        self.data[kind] = value

    def files(self, active):
        c._active(active)
        return self.paths


def test_windows_text_image_and_file_formats_without_real_clipboard(tmp_path, monkeypatch):
    monkeypatch.setattr(c.sys, "platform", "win32")
    api = FakeWindows({13: "Olá\x00".encode("utf-16-le")})
    monkeypatch.setattr(c, "_Windows", lambda: api)
    assert c.read_payload_sync(lambda: True) == c.ClipboardPayload("text", "Olá")
    assert c.write_payload_sync(c.ClipboardPayload("text", "é"), lambda: True) == 2
    assert api.data[13] == "é\x00".encode("utf-16-le")
    image = png()
    assert c.write_payload_sync(c.ClipboardPayload("image", image), lambda: True) == len(image)
    assert api.data[50001] == image and {8, 17}.issubset(api.data)
    with Image.open(BytesIO(c._dib_png(api.data[17]))) as decoded:
        assert decoded.size == (40, 30)
    assert c.read_payload_sync(lambda: True) == c.ClipboardPayload("image", image)
    api.data[50001] = image + b"unrelated allocation padding"
    assert c.read_payload_sync(lambda: True) == c.ClipboardPayload("image", image)
    paths = (str(tmp_path / "ação.txt"),)
    c.write_payload_sync(c.ClipboardPayload("files", paths), lambda: True)
    assert struct.unpack_from("<IiiII", api.data[15]) == (20, 0, 0, 0, 1)
    assert api.data[15][20:].decode("utf-16-le") == paths[0] + "\x00\x00"
    assert api.data[50002] == struct.pack("<I", 1)
    api.paths = paths
    assert c.read_payload_sync(lambda: True) == c.ClipboardPayload("files", paths)
    assert c.sequence_number() == 123


@pytest.mark.parametrize("header_size", [40, 124])
def test_reads_dib_fallback_and_bounds_pixels(monkeypatch, header_size):
    out = BytesIO()
    Image.new("RGB", (24, 12), "red").save(out, "BMP")
    data = out.getvalue()[14:]
    if header_size == 124:
        header = bytearray(124)
        header[:40] = data[:40]
        struct.pack_into("<I", header, 0, 124)
        data = bytes(header) + data[40:]
    api = FakeWindows({17 if header_size == 124 else 8: data})
    monkeypatch.setattr(c, "_Windows", lambda: api)
    value = c._windows_read(lambda: True)
    assert value.kind == "image"
    with Image.open(BytesIO(value.value)) as image:
        assert image.size == (24, 12)
    broken = bytearray(data)
    struct.pack_into("<ii", broken, 4, 100000, 100000)
    with pytest.raises(c.ClipboardError):
        c._dib_png(bytes(broken))


def test_windows_api_uses_pointer_sized_handles_and_closes_after_cancel(monkeypatch):
    libraries = {}
    def library(name, **kwargs):
        if name not in libraries:
            libraries[name] = SimpleNamespace()
        result = libraries[name]
        from unittest.mock import MagicMock
        result = libraries[name] = MagicMock()
        result.OpenClipboard.return_value = True
        result.CreateWindowExW.return_value = 0x100000123
        return result
    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    api = c._Windows()
    assert api.user.GetClipboardData.restype is ctypes.c_void_p
    assert api.kernel.GlobalAlloc.restype is ctypes.c_void_p
    assert api.kernel.GlobalLock.restype is ctypes.c_void_p
    assert api.kernel.GlobalSize.restype is ctypes.c_size_t
    states = iter((True, True, False))
    with pytest.raises(c.ClipboardError), api.opened(lambda: next(states), write=True):
        pytest.fail("Cancelled clipboard must not be yielded")
    api.user.CloseClipboard.assert_called_once()
    api.user.DestroyWindow.assert_called_once_with(0x100000123)


@pytest.mark.parametrize("kind", ["text", "image", "files"])
def test_inactive_payload_does_not_touch_native_clipboard(monkeypatch, kind, tmp_path):
    native = Mock(side_effect=AssertionError("native clipboard"))
    monkeypatch.setattr(c, "_Windows", native)
    monkeypatch.setattr(c, "_xclip_read", native)
    monkeypatch.setattr(c, "_xclip_write", native)
    value = {"text": "hello", "image": png(), "files": (str(tmp_path / "file"),)}[kind]
    with pytest.raises(c.ClipboardError):
        c.write_payload_sync(c.ClipboardPayload(kind, value), lambda: False)
    with pytest.raises(c.ClipboardError):
        c.read_payload_sync(lambda: False)
    native.assert_not_called()


def test_linux_png_and_utf8_targets_are_bounded_without_real_clipboard(monkeypatch):
    monkeypatch.setattr(c.sys, "platform", "linux")
    image = png()
    calls = []
    def read(target, limit, active):
        calls.append((target, limit))
        return b"image/png\nUTF8_STRING\n" if target == "TARGETS" else image
    monkeypatch.setattr(c, "_xclip_read", read)
    assert c.read_payload_sync(lambda: True) == c.ClipboardPayload("image", image)
    assert calls[-1] == ("image/png", c.IMAGE_LIMIT)
    write = Mock()
    monkeypatch.setattr(c, "_xclip_write", write)
    assert c.write_payload_sync(c.ClipboardPayload("text", "ação"), lambda: True) == 6
    assert write.call_args.args[:2] == ("UTF8_STRING", "ação".encode())
    assert c.sequence_number() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX URI path semantics")
def test_linux_file_uris_only_allow_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(c.sys, "platform", "linux")
    target = tmp_path / "ação com espaço.png"
    uri = target.as_uri().encode()
    assert c._uri_paths(uri) == (str(target),)
    for value in (b"https://example.com/file", b"file://remote-host/tmp/file", b"file:/tmp/file", b"file:///tmp/file?x=1", b"file:///tmp/a%00b"):
        with pytest.raises(c.ClipboardError):
            c._uri_paths(value)


def test_payload_validation_and_async_worker_contract(monkeypatch):
    for payload in (c.ClipboardPayload("image", b"not png"), c.ClipboardPayload("files", ("relative.txt",)),
                    c.ClipboardPayload("text", "é" * c.MAX_BYTES), c.ClipboardPayload("text", "a\x00b"),
                    c.ClipboardPayload("unknown", "value")):
        with pytest.raises(c.ClipboardError):
            c.check_payload(payload)
    payload = c.ClipboardPayload("image", png())
    monkeypatch.setattr(c, "read_payload_sync", Mock(return_value=payload))
    monkeypatch.setattr(c, "write_payload_sync", Mock(return_value=len(payload.value)))
    assert asyncio.run(c.read_payload(lambda: True)) == payload
    assert asyncio.run(c.write_payload(payload, lambda: True)) == len(payload.value)
