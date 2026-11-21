import asyncio
import hashlib
import json
import os
import stat
import re
import threading
import time
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Callable
from io import BytesIO
from queue import Empty, Full, Queue

import aiohttp
from PIL import Image

from .discovery import FP
from .filetransfer import BLOCK, valid_name, valid_size
from .security import COMBOS, private_ip, valid_chat
from .updater import session_lease
from .wol import mac_bytes
from .trust import Changed, Trust
from .diagnostics import event
from . import clipboard
from .certs import data_dir
from .clipboard_transport import CHUNK, MIME, blocking
from .clipboard_bundle import MAX_BUNDLE_BYTES, create_bundle, apply_bundle, inspect_bundle_kind


class NeedPin(ValueError):
    pass


async def no_redirect(*_):
    raise ValueError("Redirecionamento recusado.")


def prepare_frame(data, target):
    with Image.open(BytesIO(data), formats=["JPEG"]) as frame:
        if frame.width * frame.height > 32_000_000:
            raise ValueError("Quadro grande demais.")
        if target is None or (frame.width <= target[0] and frame.height <= target[1]):
            return frame.convert("RGB")
        scale = min(target[0] / frame.width, target[1] / frame.height)
        size = tuple(max(1, round(n * scale)) for n in frame.size)
        frame.draft("RGB", size)
        image = frame.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        return image if image.mode == "RGB" else image.convert("RGB")


@dataclass
class Upload:
    path: Path
    progress: Callable[[int, int], None] | None
    cancel: threading.Event = field(default_factory=threading.Event)


class OutgoingQueue(Queue):
    """Keep command barriers in order, replacing only adjacent pointer motion."""
    def __init__(self):
        super().__init__(512)
        self.dropped_moves = 0

    def put_nowait(self, message):
        with self.not_full:
            if message.get("t") == "move" and self.queue and self.queue[-1].get("t") == "move":
                self.queue[-1] = message
                self.dropped_moves += 1
            else:
                if self._qsize() >= self.maxsize:
                    raise Full
                self._put(message)
                self.unfinished_tasks += 1
                self.not_empty.notify()

    def command_waiting(self):
        with self.mutex:
            return any(message.get("t") != "move" for message in self.queue)

    def dropped(self):
        with self.mutex:
            self.dropped_moves += 1


REFUSALS = {
    403: "O PC remoto não permite essa transferência (arquivos desativados ou modo somente ver).",
    409: "Aguarde a transferência atual terminar.",
    413: "Conteúdo acima do limite: arquivos até 512 MiB, imagens até 32 MiB.",
}


async def refusal(response):
    """Mensagem para o usuário a partir da recusa HTTP do agente."""
    if response.status in REFUSALS:
        return REFUSALS[response.status]
    text = ""
    if response.content_type == "text/plain" and (response.content_length or 0) <= 300:
        try:
            text = (await response.content.read(300)).decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            text = ""
    if text and text.isprintable() and not text.startswith(("400", "Bad Request")):
        return text
    return "Área de transferência recusada pelo PC remoto."


class Client:
    """Uma tentativa por instância. A interface consome events na sua própria thread."""
    def __init__(self, peer):
        self.peer = peer
        self.target = (1, 1)
        self.events = Queue(2)
        self.frames = Queue(1)
        self.outgoing = OutgoingQueue()
        self.closed = threading.Event()
        self.files = False
        self._upload = None
        self._lock = threading.Lock()
        self._loop = self._write_ready = None
        self._motion_interrupt = threading.Event()
        self._motion_throttled = False
        self.frame_epoch = 0
        self.decode_ms = 0
        self.clipboard_rich = self._clipboard_enabled = False
        self._clipboard_read_only = self._clipboard_busy = False
        self._clipboard_context = self._clipboard_task = None

    def set_view_only(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Modo inválido.")
        self._clipboard_read_only = enabled
        self.send({"t": "view_only", "enabled": enabled})

    def paste_clipboard(self, paste_after=False):
        return self._schedule_clipboard("paste", paste_after)

    def copy_clipboard(self, copy_first=False, cut=False):
        if type(cut) is not bool:
            raise ValueError("Opção de área de transferência inválida.")
        cut = cut and copy_first is True and getattr(self, "clipboard_cut", False)
        return self._schedule_clipboard("copy", "cut" if cut else copy_first)

    def clipboard_from_remote(self):
        """True quando a área de transferência local ainda é a que veio do remoto.

        Nesse caso Ctrl+V deve colar no remoto o que ele já tem, sem reenviar
        (reenviar perderia formatos como células do Excel e anularia o Ctrl+X).
        """
        current = clipboard.sequence_number()
        return current is not None and current == getattr(self, "_pulled_sequence", None)

    def _schedule_clipboard(self, direction, shortcut):
        if type(shortcut) is not bool and not (direction == "copy" and shortcut == "cut"):
            raise ValueError("Opção de área de transferência inválida.")
        with self._lock:
            context = self._clipboard_context
            if (context is None or self.closed.is_set() or not self.clipboard_rich or not self._clipboard_enabled
                    or self._clipboard_read_only and (direction == "paste" or shortcut)):
                raise ValueError("Área de transferência indisponível nesta sessão.")
            if self._clipboard_busy:
                return False
            self._clipboard_busy = True
            operation = self._transfer_clipboard(context, direction, shortcut)
            try:
                asyncio.run_coroutine_threadsafe(operation, context[0])
            except RuntimeError:
                operation.close()
                self._clipboard_busy = False
                raise ValueError("Sessão encerrada.") from None
        return True

    async def _transfer_clipboard(self, context, direction, shortcut):
        loop, http, fingerprint, base, token = context
        self._clipboard_task = asyncio.current_task()
        cancelled = threading.Event()
        kind = None

        def active():
            return (not cancelled.is_set() and self._clipboard_context is context and not self.closed.is_set()
                    and self._clipboard_enabled and (direction != "paste" or not self._clipboard_read_only)
                    and (kind != "files" or self.files))

        def check():
            if not active():
                raise clipboard.ClipboardError("Área de transferência indisponível.")

        try:
            with tempfile.TemporaryDirectory(prefix="markcontrol-clipboard-") as folder:
                path = Path(folder) / "clipboard.bundle"
                check()
                headers = {"X-Mark-Session": token}
                timeout = aiohttp.ClientTimeout(total=300, sock_connect=5, sock_read=30)
                url = f"https://{base}/api/clipboard"
                if direction == "paste":
                    payload = await clipboard.read_payload(active)
                    kind = payload.kind
                    check()
                    await blocking(create_bundle, payload, path, active, cancelled=cancelled)
                    check()
                    size = path.stat().st_size
                    if size > MAX_BUNDLE_BYTES:
                        raise clipboard.ClipboardError("Área de transferência acima do limite.")

                    async def body():
                        with path.open("rb") as source:
                            while True:
                                check()
                                data = await blocking(source.read, CHUNK, cancelled=cancelled)
                                if not data:
                                    return
                                yield data

                    headers.update({"Content-Type": MIME, "Content-Length": str(size)})
                    async with http.put(url, params={"paste_after": "1" if shortcut else "0"},
                                        headers=headers, data=body(), ssl=fingerprint, allow_redirects=False,
                                        timeout=timeout) as response:
                        if response.status != 200:
                            raise clipboard.ClipboardError(await refusal(response))
                        length = response.content_length
                        if length is None or not 0 < length <= 1024:
                            raise clipboard.ClipboardError("Confirmação de transferência inválida.")
                        raw = await response.content.readexactly(length)
                        if json.loads(raw) != {"ok": True}:
                            raise clipboard.ClipboardError("Confirmação de transferência inválida.")
                else:
                    params = {"copy_first": "1" if shortcut else "0"}
                    if shortcut == "cut":
                        params["cut"] = "1"
                    async with http.get(url, params=params,
                                        headers=headers, ssl=fingerprint, allow_redirects=False,
                                        timeout=timeout) as response:
                        if response.status != 200:
                            raise clipboard.ClipboardError(await refusal(response))
                        if (response.content_type != MIME
                                or response.content_length is not None and response.content_length > MAX_BUNDLE_BYTES):
                            raise clipboard.ClipboardError("Área de transferência acima do limite.")
                        received = 0
                        with path.open("xb") as output:
                            async for data in response.content.iter_chunked(CHUNK):
                                check()
                                received += len(data)
                                if received > MAX_BUNDLE_BYTES:
                                    raise clipboard.ClipboardError("Área de transferência acima do limite.")
                                await blocking(output.write, data, cancelled=cancelled)
                    check()
                    kind = await blocking(inspect_bundle_kind, path, cancelled=cancelled)
                    check()
                    payload = await blocking(apply_bundle, path, data_dir() / "clipboard-received", active, cancelled=cancelled)
                    check()
                    await clipboard.write_payload(payload, active)
                    self._pulled_sequence = clipboard.sequence_number()
                if self._clipboard_context is context and not self.closed.is_set():
                    await self.emit("clipboard_ok", {"direction": direction, "kind": kind})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            event("clipboard_failed", exc)
            if self._clipboard_context is context and not self.closed.is_set():
                detail = str(exc) if isinstance(exc, clipboard.ClipboardError) and str(exc) else (
                    "Não foi possível transferir a área de transferência. Verifique a conexão e as permissões.")
                await self.emit("clipboard_error", detail)
        finally:
            cancelled.set()
            with self._lock:
                self._clipboard_busy = False
            if self._clipboard_task is asyncio.current_task():
                self._clipboard_task = None

    def send_combo(self, keys):
        if keys not in COMBOS:
            raise ValueError("Combinação inválida.")
        self.send({"t": "combo", "keys": list(keys)})

    def send_chat(self, text):
        if not valid_chat(text):
            raise ValueError("Mensagem inválida.")
        self.send({"t": "chat", "s": text})

    def send_file(self, path, progress_cb=None):
        path = Path(path)
        if not valid_name(path.name) or (progress_cb is not None and not callable(progress_cb)):
            raise ValueError("Parâmetros de arquivo inválidos.")
        with self._lock:
            if self.closed.is_set() or not self.files or self._upload is not None:
                raise ValueError("Envio de arquivo indisponível.")
            self._upload = Upload(path, progress_cb)

    def cancel_file(self):
        with self._lock:
            if self._upload:
                self._upload.cancel.set()

    async def _send_upload(self, ws, job, replies):
        async def expect(kind):
            result = await asyncio.wait_for(replies.get(), 30)
            if result == "file_error":
                raise ValueError("Arquivo recusado pelo agente.")
            if result != kind:
                raise RuntimeError("Confirmação de arquivo inesperada.")

        if job.cancel.is_set():
            return "file_cancelled"
        if not job.path.is_file():
            raise ValueError("Selecione um arquivo regular.")
        with job.path.open("rb") as source:
            info = os.fstat(source.fileno())
            total = info.st_size
            if not stat.S_ISREG(info.st_mode) or not valid_size(total):
                raise ValueError("Arquivo inválido ou grande demais.")
            await ws.send_json({"t": "file_begin", "name": job.path.name, "size": total})
            await expect("file_ready")
            digest = hashlib.sha256()
            sent = 0
            while sent < total and not job.cancel.is_set():
                count = min(BLOCK, total - sent)
                try:
                    data = source.read(count)
                except OSError:
                    data = b""
                if len(data) != count:
                    await ws.send_json({"t": "file_cancel"})
                    await expect("file_cancelled")
                    raise ValueError("Falha na leitura do arquivo.")
                await ws.send_bytes(data)
                await expect("file_ready")
                digest.update(data)
                sent += count
                if job.progress is not None:
                    try:
                        job.progress(sent, total)
                    except Exception:
                        await ws.send_json({"t": "file_cancel"})
                        await expect("file_cancelled")
                        raise ValueError("Falha no callback de progresso.") from None
            if job.cancel.is_set():
                await ws.send_json({"t": "file_cancel"})
                await expect("file_cancelled")
                return "file_cancelled"
            await ws.send_json({"t": "file_end", "sha256": digest.hexdigest()})
            await expect("file_ok")
            return "file_ok"

    async def _uploads(self, ws, replies):
        while True:
            with self._lock:
                job = self._upload
            if job is None:
                await asyncio.sleep(0.025)
                continue
            try:
                result = await self._send_upload(ws, job, replies)
            except (TimeoutError, ConnectionError):
                raise
            except (ValueError, FileNotFoundError, PermissionError):
                result = "file_error"
            finally:
                with self._lock:
                    self._upload = None
            await self.emit(result)

    def start(self, pin=None, retrust=False):
        threading.Thread(target=self._run, args=(pin, retrust), daemon=True).start()

    def close(self):
        self.closed.set()
        self._motion_interrupt.set()

    def send(self, message):
        if self.closed.is_set():
            return
        try:
            self.outgoing.put_nowait(message)
            motion = message.get("t") == "move"
            if not motion:
                self._motion_interrupt.set()
            if self._loop is not None and self._write_ready is not None and (not motion or not self._motion_throttled):
                try:
                    self._loop.call_soon_threadsafe(self._write_ready.set)
                except RuntimeError:
                    pass
        except Full as exc:
            if message["t"] != "move":
                event("viewer_failed", exc)
                self.close()
            else:
                self.outgoing.dropped()

    async def emit(self, kind, value=None):
        if kind == "frame":
            try:
                self.frames.get_nowait()
            except Empty:
                pass
            self.frames.put_nowait(value)
            return
        while not self.closed.is_set():
            try:
                self.events.put_nowait((kind, value))
                return
            except Full:
                await asyncio.sleep(0.015)

    async def write_stream(self, ws):
        self._loop = asyncio.get_running_loop()
        self._write_ready = asyncio.Event()
        pending_move, next_motion = None, 0
        try:
            while True:
                self._write_ready.clear()
                while True:
                    try:
                        message = self.outgoing.get_nowait()
                    except Empty:
                        break
                    if message.get("t") == "move":
                        if pending_move is not None:
                            self.outgoing.dropped()
                        pending_move = message
                        continue
                    if pending_move is not None:
                        await ws.send_json(pending_move)
                        pending_move = None
                        next_motion = time.perf_counter() + 1 / 60
                    await ws.send_json(message)
                if pending_move is not None:
                    remaining = next_motion - time.perf_counter()
                    if remaining > 0:
                        self._motion_throttled = True
                        self._motion_interrupt.clear()
                        try:
                            if not self.outgoing.command_waiting():
                                # OS wait has a real deadline on Windows, unlike
                                # sub-clock-resolution asyncio sleep callbacks.
                                # Key/button barriers interrupt it immediately.
                                await asyncio.to_thread(self._motion_interrupt.wait, remaining)
                        finally:
                            self._motion_throttled = False
                        continue
                    await ws.send_json(pending_move)
                    pending_move = None
                    next_motion = time.perf_counter() + 1 / 60
                await self._write_ready.wait()
        finally:
            self._motion_throttled = False
            self._motion_interrupt.set()
            self._write_ready = self._loop = None

    async def decode_stream(self, encoded, ready):
        while True:
            await ready.wait()
            ready.clear()
            if not encoded:
                continue  # A monitor/info change can invalidate a signalled frame.
            epoch, data = encoded.pop()
            started = time.perf_counter()
            image = await asyncio.to_thread(prepare_frame, data, self.target)
            self.decode_ms = round((time.perf_counter() - started) * 1000)
            if epoch == self.frame_epoch:
                await self.emit("frame", image)

    def _run(self, pin, retrust):
        async def work():
            task = asyncio.create_task(self.connect(pin, retrust))
            try:
                while not task.done() and not self.closed.is_set():
                    await asyncio.sleep(0.025)
                if not task.done():
                    task.cancel()
                await task
            except asyncio.CancelledError:
                pass
            except NeedPin as exc:
                await self.emit("pin", str(exc))
            except Changed:
                await self.emit("changed", self.peer)
            except aiohttp.ServerFingerprintMismatch as exc:
                await self.emit("changed", replace(self.peer, fingerprint=exc.got.hex(":")))
            except Exception as exc:
                event("viewer_failed", exc)
                await self.emit("error", str(exc) or "Conexão interrompida.")
            else:
                await self.emit("end")
            finally:
                self.closed.set()
        asyncio.run(work())

    async def connect(self, pin, retrust):
        with session_lease():
            await self._connect(pin, retrust)

    async def _connect(self, pin, retrust):
        peer = self.peer
        if not private_ip(peer.ip) or ":" in peer.ip or not FP.fullmatch(peer.fingerprint):
            raise ValueError("Destino inválido.")
        trust = Trust()
        key = trust.key(peer, retrust)
        fingerprint = aiohttp.Fingerprint(bytes.fromhex(peer.fingerprint.replace(":", "")))
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(no_redirect)
        timeout = aiohttp.ClientTimeout(total=12, sock_connect=5)
        base = f"{peer.ip}:{peer.port}"
        async with aiohttp.ClientSession(trace_configs=[trace], timeout=timeout,
                                         trust_env=False, auto_decompress=False) as http:
            async with http.head(f"https://{base}/", ssl=fingerprint, allow_redirects=False) as proof:
                if proof.status != 200:
                    raise ValueError("Identidade HTTPS indisponível.")
            trust.verified(peer, key, retrust)
            if pin is not None:
                body = {"pin": pin, "remember": True}
            elif key and peer.pin_required:
                body = {"device_key": key}
            elif peer.pin_required:
                raise NeedPin("Informe o PIN deste PC.")
            else:
                body = {}
            async with http.post(f"https://{base}/api/login", json=body, ssl=fingerprint,
                                 allow_redirects=False) as response:
                if response.status == 403 and response.content_type == "application/json":
                    length = response.content_length
                    if length is not None and 0 < length <= 1024:
                        detail = json.loads(await response.content.readexactly(length))
                        if detail == {"error": "Não dá para controlar o próprio PC"}:
                            raise ValueError(detail["error"])
                if response.status == 401:
                    raise NeedPin("PIN incorreto. Confira os 8 dígitos e tente de novo." if pin is not None
                                  else "Informe o PIN para parear este PC.")
                if response.status != 200:
                    raise ValueError({409: "PC em uso.", 429: "Tentativas bloqueadas."}.get(
                        response.status, f"Login recusado (HTTP {response.status})."))
                length = response.content_length
                if length is None or not 0 < length <= 1024:
                    raise ValueError("Resposta de login inválida.")
                result = json.loads(await response.content.readexactly(length))
            token = result["token"]
            if not isinstance(token, str) or not re.fullmatch("[0-9a-f]{64}", token):
                raise ValueError("Token inválido.")
            if "device_key" in result:
                if pin is None or not re.fullmatch("[0-9a-f]{64}", result["device_key"]):
                    raise ValueError("Pareamento inválido.")
                trust.verified(peer, result["device_key"])
            async with http.ws_connect(f"wss://{base}/ws", protocols=("markcontrol", token),
                                       ssl=fingerprint, heartbeat=20, compress=0,
                                       max_msg_size=32 << 20) as ws:
                if ws.protocol != "markcontrol":
                    raise ValueError("Protocolo inesperado.")
                self._clipboard_context = (asyncio.get_running_loop(), http, fingerprint, base, token)
                replies = asyncio.Queue(1)
                encoded, frame_ready = [], asyncio.Event()

                async def read():
                    async for packet in ws:
                        if packet.type == aiohttp.WSMsgType.BINARY:
                            encoded[:] = [(self.frame_epoch, packet.data)]
                            frame_ready.set()
                        elif packet.type == aiohttp.WSMsgType.TEXT:
                            if len(packet.data) > 620000:
                                raise ValueError("Mensagem grande demais.")
                            message = json.loads(packet.data)
                            if not isinstance(message, dict):
                                raise ValueError("Mensagem inválida.")
                            kind = message.get("t")
                            if kind in ("file_ready", "file_ok", "file_error", "file_cancelled"):
                                if len(message) != 1 or self._upload is None or replies.full():
                                    raise ValueError("Confirmação de arquivo inválida.")
                                replies.put_nowait(kind)
                            elif kind == "chat":
                                if message.keys() != {"t", "s"} or not valid_chat(message["s"]):
                                    raise ValueError("Resposta de chat inválida.")
                                await self.emit("chat", message["s"])
                            else:
                                if kind == "info":
                                    self.frame_epoch += 1
                                    encoded.clear()
                                    frame_ready.clear()
                                    try:
                                        self.frames.get_nowait()
                                    except Empty:
                                        pass
                                    caps = message.get("features", {})
                                    if not isinstance(caps, dict) or any(type(v) is not bool for v in caps.values()):
                                        raise ValueError("Capacidades inválidas.")
                                    if "mac" in message:
                                        mac_bytes(message["mac"])
                                    self.files = caps.get("files", False)
                                    self.clipboard_rich = caps.get("clipboard_rich", False)
                                    self.clipboard_cut = caps.get("clipboard_cut", False)
                                    self._clipboard_enabled = message.get("clipboard") is True
                                elif kind == "view_only":
                                    self._clipboard_read_only = message.get("enabled") is True
                                await self.emit("message", message)
                        elif packet.type == aiohttp.WSMsgType.ERROR:
                            raise ValueError("Conexão interrompida.")

                tasks = [asyncio.create_task(read()), asyncio.create_task(self.write_stream(ws)),
                         asyncio.create_task(self.decode_stream(encoded, frame_ready)),
                         asyncio.create_task(self._uploads(ws, replies))]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    self._clipboard_context = None
                    if self._clipboard_task is not None:
                        self._clipboard_task.cancel()
                        await asyncio.gather(self._clipboard_task, return_exceptions=True)
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
