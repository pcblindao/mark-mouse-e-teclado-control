import asyncio
import ctypes
import os
import selectors
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

import pyperclip
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 100 * 1024
IMAGE_LIMIT = 32 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_ENTRIES = 256


@dataclass(frozen=True)
class ClipboardPayload:
    kind: str
    value: object


class ClipboardError(ValueError):
    pass


def check_text(value):
    if not isinstance(value, str):
        raise ClipboardError("Texto inválido.")
    if len(value) > MAX_BYTES:
        raise ClipboardError("Texto acima de 100 KiB.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ClipboardError("UTF-8 inválido.") from None
    if size > MAX_BYTES:
        raise ClipboardError("Texto acima de 100 KiB.")
    return size


def _transfer(active, value):
    try:
        if not active():
            raise ClipboardError("Sessão encerrada.")
        if value is None:
            value = pyperclip.paste()
        else:
            pyperclip.copy(value)
        return value, check_text(value)
    except (pyperclip.PyperclipException, OSError, ValueError):
        raise ClipboardError("Área de transferência indisponível ou texto inválido.") from None


async def read_text(active):
    return await asyncio.to_thread(_transfer, active, None)


async def write_text(value, active):
    check_text(value)
    _, size = await asyncio.to_thread(_transfer, active, value)
    return size


def _active(active):
    if not active():
        raise ClipboardError("Sessão encerrada.")


def check_png(value):
    if not isinstance(value, bytes) or not 0 < len(value) <= IMAGE_LIMIT:
        raise ClipboardError("Imagem acima de 32 MiB ou inválida.")
    try:
        with Image.open(BytesIO(value), formats=["PNG"]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ClipboardError("Imagem acima de 32 megapixels.")
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError, UnidentifiedImageError):
        raise ClipboardError("Imagem PNG inválida ou acima do limite.") from None
    return len(value)


def check_paths(value):
    if (not isinstance(value, tuple) or not 1 <= len(value) <= MAX_ENTRIES
            or not all(isinstance(path, str) and path and "\x00" not in path
                       and Path(path).is_absolute() for path in value)):
        raise ClipboardError("Lista de arquivos inválida.")
    return sum(len(path.encode("utf-8")) for path in value)


def check_payload(payload):
    if not isinstance(payload, ClipboardPayload):
        raise ClipboardError("Conteúdo da área de transferência inválido.")
    if payload.kind == "text":
        size = check_text(payload.value)
        if "\x00" in payload.value:
            raise ClipboardError("Texto com caractere nulo não pode ser copiado.")
        return size
    if payload.kind == "image":
        return check_png(payload.value)
    if payload.kind == "files":
        return check_paths(payload.value)
    raise ClipboardError("Tipo de área de transferência indisponível.")


class _Windows:
    def __init__(self):
        from ctypes import wintypes as wt
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.user.OpenClipboard.argtypes, self.user.OpenClipboard.restype = [wt.HWND], wt.BOOL
        self.user.CloseClipboard.argtypes, self.user.CloseClipboard.restype = [], wt.BOOL
        self.user.EmptyClipboard.argtypes, self.user.EmptyClipboard.restype = [], wt.BOOL
        self.user.IsClipboardFormatAvailable.argtypes, self.user.IsClipboardFormatAvailable.restype = [wt.UINT], wt.BOOL
        self.user.GetClipboardData.argtypes, self.user.GetClipboardData.restype = [wt.UINT], wt.HANDLE
        self.user.SetClipboardData.argtypes, self.user.SetClipboardData.restype = [wt.UINT, wt.HANDLE], wt.HANDLE
        self.user.RegisterClipboardFormatW.argtypes, self.user.RegisterClipboardFormatW.restype = [wt.LPCWSTR], wt.UINT
        self.user.GetClipboardSequenceNumber.argtypes, self.user.GetClipboardSequenceNumber.restype = [], wt.DWORD
        self.user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
        self.user.CreateWindowExW.restype = wt.HWND
        self.user.DestroyWindow.argtypes, self.user.DestroyWindow.restype = [wt.HWND], wt.BOOL
        self.kernel.GlobalAlloc.argtypes, self.kernel.GlobalAlloc.restype = [wt.UINT, ctypes.c_size_t], wt.HANDLE
        self.kernel.GlobalSize.argtypes, self.kernel.GlobalSize.restype = [wt.HANDLE], ctypes.c_size_t
        self.kernel.GlobalLock.argtypes, self.kernel.GlobalLock.restype = [wt.HANDLE], ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes, self.kernel.GlobalUnlock.restype = [wt.HANDLE], wt.BOOL
        self.kernel.GlobalFree.argtypes, self.kernel.GlobalFree.restype = [wt.HANDLE], wt.HANDLE
        self.shell.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
        self.shell.DragQueryFileW.restype = wt.UINT

    @contextmanager
    def opened(self, active, write=False):
        owner = None
        try:
            if write:
                _active(active)
                owner = self.user.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0,
                                                 ctypes.c_void_p(-3), None, None, None)
                if not owner:
                    raise ClipboardError("Proprietário da área de transferência indisponível.")
            for attempt in range(10):
                _active(active)
                if self.user.OpenClipboard(owner):
                    break
                if attempt == 9:
                    raise ClipboardError("Área de transferência ocupada.")
                time.sleep(0.01)
            try:
                _active(active)
                yield self
            finally:
                self.user.CloseClipboard()
        finally:
            if owner:
                self.user.DestroyWindow(owner)

    def register(self, name):
        result = self.user.RegisterClipboardFormatW(name)
        if not result:
            raise ClipboardError("Formato da área de transferência indisponível.")
        return result

    def has(self, kind):
        return bool(self.user.IsClipboardFormatAvailable(kind))

    def read(self, kind, limit):
        handle = self.user.GetClipboardData(kind)
        if not handle:
            raise ClipboardError("Área de transferência indisponível.")
        size = self.kernel.GlobalSize(handle)
        if not 0 < size <= limit:
            raise ClipboardError("Conteúdo da área de transferência acima do limite.")
        pointer = self.kernel.GlobalLock(handle)
        if not pointer:
            raise ClipboardError("Área de transferência indisponível.")
        try:
            return ctypes.string_at(pointer, size)
        finally:
            self.kernel.GlobalUnlock(handle)

    def empty(self):
        if not self.user.EmptyClipboard():
            raise ClipboardError("Área de transferência indisponível.")

    def put(self, kind, data):
        handle = self.kernel.GlobalAlloc(0x0042, len(data))
        if not handle:
            raise ClipboardError("Memória para a área de transferência indisponível.")
        try:
            pointer = self.kernel.GlobalLock(handle)
            if not pointer:
                raise ClipboardError("Memória para a área de transferência indisponível.")
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(handle)
            if not self.user.SetClipboardData(kind, handle):
                raise ClipboardError("Não foi possível escrever na área de transferência.")
            handle = None  # Windows now owns the allocation.
        finally:
            if handle:
                self.kernel.GlobalFree(handle)

    def files(self, active):
        handle = self.user.GetClipboardData(15)
        if not handle:
            raise ClipboardError("Lista de arquivos indisponível.")
        count = self.shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        if not 1 <= count <= MAX_ENTRIES:
            raise ClipboardError("Selecione até 256 arquivos ou pastas.")
        paths = []
        for index in range(count):
            _active(active)
            length = self.shell.DragQueryFileW(handle, index, None, 0)
            if not 0 < length <= 32767:
                raise ClipboardError("Nome de arquivo inválido.")
            buffer = ctypes.create_unicode_buffer(length + 1)
            if self.shell.DragQueryFileW(handle, index, buffer, length + 1) != length:
                raise ClipboardError("Lista de arquivos indisponível.")
            paths.append(buffer.value)
        return tuple(paths)


def sequence_number():
    if sys.platform != "win32":
        return None
    try:
        return int(_Windows().user.GetClipboardSequenceNumber())
    except (OSError, AttributeError):
        return None


def _dib_png(data):
    if len(data) < 12:
        raise ClipboardError("Imagem DIB inválida.")
    header = struct.unpack_from("<I", data)[0]
    if header == 12:
        width, height, planes, depth = struct.unpack_from("<HHHH", data, 4)
        offset = header + ((1 << depth) * 3 if depth <= 8 else 0)
    elif header in (40, 52, 56, 108, 124) and len(data) >= header:
        width, height, planes, depth = struct.unpack_from("<iiHH", data, 4)
        compression, colors = struct.unpack_from("<I", data, 16)[0], struct.unpack_from("<I", data, 32)[0]
        masks = 12 if header == 40 and compression == 3 else 16 if header == 40 and compression == 6 else 0
        offset = header + masks + (colors or (1 << depth) if depth <= 8 else colors) * 4
    else:
        raise ClipboardError("Imagem DIB indisponível.")
    if planes != 1 or width <= 0 or height == 0 or width * abs(height) > MAX_PIXELS or offset > len(data):
        raise ClipboardError("Imagem DIB inválida ou acima do limite.")
    bmp = b"BM" + struct.pack("<IHHI", len(data) + 14, 0, 0, offset + 14) + data
    try:
        with Image.open(BytesIO(bmp), formats=["BMP"]) as image:
            image.load()
            out = BytesIO()
            image.save(out, "PNG")
            result = out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ClipboardError("Imagem DIB inválida.") from None
    if len(result) > IMAGE_LIMIT:
        raise ClipboardError("Imagem acima de 32 MiB.")
    return result


def _png_content(data):
    # HGLOBAL may be rounded up by the allocator. Never transmit padding after
    # IEND: those bytes are not part of the image selected by the user.
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ClipboardError("Imagem PNG inválida.")
    position = 8
    while position + 12 <= len(data):
        length = struct.unpack_from(">I", data, position)[0]
        end = position + 12 + length
        if end > len(data):
            break
        if data[position + 4:position + 8] == b"IEND":
            if length:
                break
            return data[:end]
        position = end
    raise ClipboardError("Imagem PNG incompleta.")


def _windows_read(active):
    api = _Windows()
    with api.opened(active):
        if api.has(15):
            return ClipboardPayload("files", api.files(active))
        png = api.register("PNG")
        if api.has(png):
            value = _png_content(api.read(png, IMAGE_LIMIT))
            return ClipboardPayload("image", value)
        for kind in (17, 8):
            if api.has(kind):
                return ClipboardPayload("image", _dib_png(api.read(kind, MAX_PIXELS * 4 + 65536)))
        if api.has(13):
            raw = api.read(13, MAX_BYTES * 2 + 2)
            if len(raw) % 2:
                raise ClipboardError("Texto Unicode inválido.")
            value = raw.decode("utf-16-le").split("\x00", 1)[0]
            check_text(value)
            return ClipboardPayload("text", value)
        raise ClipboardError("Copie um texto, imagem ou arquivos antes de colar.")


def _windows_write(payload, active, size=None):
    size = check_payload(payload) if size is None else size
    formats = []
    api = _Windows()
    if payload.kind == "text":
        formats.append((13, payload.value.encode("utf-16-le") + b"\x00\x00"))
    elif payload.kind == "image":
        formats.append((api.register("PNG"), payload.value))
        with Image.open(BytesIO(payload.value)) as image:
            rgba = image.convert("RGBA")
            bmp = BytesIO()
            white = Image.new("RGB", image.size, "white")
            white.paste(rgba, mask=rgba.getchannel("A"))
            white.save(bmp, "BMP")
            formats.append((8, bmp.getvalue()[14:]))
            # BITMAPV5HEADER, top-down BGRA with explicit masks and sRGB.
            header = bytearray(124)
            struct.pack_into("<IiiHHIIiiII", header, 0, 124, image.width, -image.height, 1, 32, 3,
                             image.width * image.height * 4, 0, 0, 0, 0)
            struct.pack_into("<IIIII", header, 40, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000, 0x73524742)
            formats.append((17, bytes(header) + rgba.tobytes("raw", "BGRA")))
    else:
        raw = ("\x00".join(payload.value) + "\x00\x00").encode("utf-16-le")
        formats.append((15, struct.pack("<IiiII", 20, 0, 0, 0, 1) + raw))
        formats.append((api.register("Preferred DropEffect"), struct.pack("<I", 1)))
    with api.opened(active, write=True):
        _active(active)
        api.empty()
        for kind, value in formats:
            _active(active)
            api.put(kind, value)
    return size


def _xclip_read(target, limit, active):
    _active(active)
    process = subprocess.Popen(["xclip", "-selection", "clipboard", "-o", "-t", target],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline, result = time.monotonic() + 3, bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                _active(active)
                if time.monotonic() >= deadline:
                    raise ClipboardError("A área de transferência não respondeu.")
                if not selector.select(0.05):
                    continue
                data = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(result)))
                if not data:
                    break
                result.extend(data)
                if len(result) > limit:
                    raise ClipboardError("Área de transferência acima do limite.")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise ClipboardError("Formato da área de transferência indisponível.")
        return bytes(result)
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.wait(timeout=1)


def _xclip_write(target, data, active):
    _active(active)
    process = subprocess.Popen(["xclip", "-selection", "clipboard", "-in", "-target", target],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 3
    try:
        os.set_blocking(process.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            position = 0
            while position < len(data):
                _active(active)
                if time.monotonic() >= deadline:
                    raise ClipboardError("A área de transferência não respondeu.")
                if selector.select(0.05):
                    try:
                        position += os.write(process.stdin.fileno(), data[position:position + 65536])
                    except BlockingIOError:
                        pass
        process.stdin.close()
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise ClipboardError("Área de transferência indisponível.")
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)


def _uri_paths(data):
    paths = []
    for line in data.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        url = urlsplit(line)
        if (not line.startswith("file://") or url.scheme != "file" or url.netloc.lower() not in ("", "localhost")
                or url.query or url.fragment):
            raise ClipboardError("Somente arquivos locais podem ser copiados.")
        path = unquote_to_bytes(url.path).decode("utf-8")
        paths.append(path)
    result = tuple(paths)
    check_paths(result)
    return result


def read_payload_sync(active):
    _active(active)
    try:
        if sys.platform == "win32":
            result = _windows_read(active)
        elif sys.platform.startswith("linux"):
            targets = _xclip_read("TARGETS", 16384, active).decode("ascii", errors="ignore").splitlines()
            if "text/uri-list" in targets:
                result = ClipboardPayload("files", _uri_paths(_xclip_read("text/uri-list", 1 << 20, active)))
            elif "image/png" in targets:
                result = ClipboardPayload("image", _xclip_read("image/png", IMAGE_LIMIT, active))
            else:
                target = next((name for name in ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain") if name in targets), None)
                if target is None:
                    raise ClipboardError("Copie um texto, imagem ou arquivos antes de colar.")
                result = ClipboardPayload("text", _xclip_read(target, MAX_BYTES, active).decode("utf-8"))
        else:
            raise ClipboardError("Área de transferência indisponível neste sistema.")
        _active(active)
        check_payload(result)
        return result
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ClipboardError("Área de transferência indisponível ou conteúdo inválido.") from None


def write_payload_sync(payload, active):
    _active(active)
    size = check_payload(payload)
    try:
        if sys.platform == "win32":
            return _windows_write(payload, active, size=size)
        if not sys.platform.startswith("linux"):
            raise ClipboardError("Área de transferência indisponível neste sistema.")
        if payload.kind == "text":
            target, data = "UTF8_STRING", payload.value.encode("utf-8")
        elif payload.kind == "image":
            target, data = "image/png", payload.value
        else:
            target, data = "text/uri-list", ("\r\n".join(Path(path).as_uri() for path in payload.value) + "\r\n").encode("utf-8")
        _xclip_write(target, data, active)
        return size
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ClipboardError("Não foi possível escrever na área de transferência.") from None


async def read_payload(active):
    return await asyncio.to_thread(read_payload_sync, active)


async def write_payload(payload, active):
    return await asyncio.to_thread(write_payload_sync, payload, active)
