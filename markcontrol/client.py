import asyncio
import hashlib
import json
import os
import stat
import re
import threading
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
from .wol import mac_bytes
from .trust import Changed, Trust


class NeedPin(ValueError):
    pass


async def no_redirect(*_):
    raise ValueError("Redirecionamento recusado.")


def prepare_frame(data, target):
    with Image.open(BytesIO(data), formats=["JPEG"]) as frame:
        if frame.width * frame.height > 32_000_000:
            raise ValueError("Quadro grande demais.")
        scale = min(target[0] / frame.width, target[1] / frame.height)
        size = tuple(max(1, round(n * scale)) for n in frame.size)
        image = frame.resize(size, Image.Resampling.BILINEAR)
        return image if image.mode == "RGB" else image.convert("RGB")


@dataclass
class Upload:
    path: Path
    progress: Callable[[int, int], None] | None
    cancel: threading.Event = field(default_factory=threading.Event)


class Client:
    """Uma tentativa por instância. A interface consome events na sua própria thread."""
    def __init__(self, peer):
        self.peer = peer
        self.target = (1, 1)
        self.events = Queue(2)
        self.outgoing = Queue(128)
        self.closed = threading.Event()
        self.files = False
        self._upload = None
        self._lock = threading.Lock()

    def set_view_only(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Modo inválido.")
        self.send({"t": "view_only", "enabled": enabled})

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

    def send(self, message):
        if self.closed.is_set():
            return
        try:
            self.outgoing.put_nowait(message)
        except Full:
            if message["t"] != "move":
                self.close()

    async def emit(self, kind, value=None):
        while not self.closed.is_set():
            try:
                self.events.put_nowait((kind, value))
                return
            except Full:
                await asyncio.sleep(0.015)

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
                await self.emit("error", str(exc) or "Conexão interrompida.")
            else:
                await self.emit("end")
            finally:
                self.closed.set()
        asyncio.run(work())

    async def connect(self, pin, retrust):
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
                if response.status == 401:
                    raise NeedPin("Informe o PIN para parear este PC.")
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
                                       max_msg_size=16 << 20) as ws:
                if ws.protocol != "markcontrol":
                    raise ValueError("Protocolo inesperado.")
                replies = asyncio.Queue(1)

                async def write():
                    while True:
                        try:
                            message = self.outgoing.get_nowait()
                        except Empty:
                            await asyncio.sleep(0.01)
                        else:
                            await ws.send_json(message)

                async def read():
                    async for packet in ws:
                        if packet.type == aiohttp.WSMsgType.BINARY:
                            await self.emit("frame", prepare_frame(packet.data, self.target))
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
                                    caps = message.get("features", {})
                                    if not isinstance(caps, dict) or any(type(v) is not bool for v in caps.values()):
                                        raise ValueError("Capacidades inválidas.")
                                    if "mac" in message:
                                        mac_bytes(message["mac"])
                                    self.files = caps.get("files", False)
                                await self.emit("message", message)
                        elif packet.type == aiohttp.WSMsgType.ERROR:
                            raise ValueError("Conexão interrompida.")

                tasks = [asyncio.create_task(read()), asyncio.create_task(write()),
                         asyncio.create_task(self._uploads(ws, replies))]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
