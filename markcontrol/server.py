import asyncio
import errno
import ipaddress
import json
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from aiohttp import WSMsgType, web

from . import capture, clipboard, history, wol
from .filetransfer import Receiver
from .certs import tls_identity
from .config import Config
from .discovery import PORT, Discovery, local_addresses
from .inputctl import Input
from .diagnostics import event
from .updater import session_lease
from .security import MAX_MESSAGE, Authorization, Session, _object, decode_message, private_ip, valid_chat

STATIC = Path(__file__).with_name("static")


@web.middleware
async def local_https(request, handler):
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if not peer or not private_ip(peer[0]) or not request.secure:
        raise web.HTTPForbidden()
    request["peer"] = peer[0]
    request["scope_id"] = peer[3] if len(peer) == 4 else 0
    if request.path in ("/api/login", "/ws"):
        origin = request.headers.get("Origin")
        # Clientes nativos podem omitir Origin. Navegadores devem usar a própria origem.
        if origin is not None and origin != f"https://{request.host}":
            raise web.HTTPForbidden()
    response = await handler(request)
    if not response.prepared:
        response.headers.update({
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self' wss:; img-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
        })
    return response


def local_source(peer, scope_id):
    ip = ipaddress.ip_address(peer)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    if ip.version == 4 and str(ip) in local_addresses():
        return True
    # O kernel também conhece endereços secundários e IPv6 com escopo.
    family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
    address = (str(ip), 0) if ip.version == 4 else (str(ip), 0, 0, scope_id)
    with socket.socket(family, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind(address)
        except OSError as exc:
            if exc.errno == errno.EADDRNOTAVAIL or getattr(exc, "winerror", None) == 10049:
                return False
            raise
    return True


@dataclass
class Control:
    session: Session
    ws: web.WebSocketResponse
    transport: asyncio.Transport
    monitors: int
    monitor: int = 1
    quality: int = 85
    fps: int = 30
    max_height: int = 0
    region: dict | None = None
    revision: int = 0
    view_only: bool = False
    upload: Receiver | None = None
    used: set = field(default_factory=set)
    next_chat: float = 0

    def use(self, kind):
        if kind not in self.used:
            self.used.add(kind)
            print(f"Função usada: {kind}", flush=True)

    def discard_upload(self):
        if self.upload is not None:
            try:
                self.upload.close()
            finally:
                self.upload = None


class Agent:
    def __init__(self, allow_clipboard, monitors, config, notify, *, files=True):
        self.auth = Authorization(config.authenticate)
        self.notify = notify
        self.input = Input()
        self.allow_clipboard = allow_clipboard
        self.allow_files = files
        self.history_path = config.path.with_name("history.json")
        self.mac = wol.local_mac()
        self.monitors = monitors
        self.control = None

    async def login(self, request):
        if os.getenv("MARKCONTROL_DEV_LOOPBACK") != "1" and await asyncio.to_thread(
            local_source, request["peer"], request.get("scope_id", 0)
        ):
            return web.json_response({"error": "Não dá para controlar o próprio PC"}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "Envie JSON."}, status=415)
        try:
            body = await request.json(loads=lambda raw: json.loads(raw, object_pairs_hook=_object))
        except (ValueError, RecursionError):
            body = None
        if self.control is not None:
            return web.json_response({"error": "Já existe uma sessão ativa."}, status=409)
        try:
            status, result = self.auth.login(request["peer"], body)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        if result:
            return web.json_response(result)
        message = "Tentativas bloqueadas temporariamente." if status == 429 else "PIN inválido."
        return web.json_response({"error": message}, status=status)

    def stop(self, control, reason):
        if self.control is control and not control.session.stopped.is_set():
            control.session.stopped.set()
            try:
                control.transport.abort()
            finally:
                self.input.release_all()
            print(f"SESSAO ENCERRADA de {control.session.peer} ({reason})", flush=True)

    def stop_active(self, reason):
        self.auth.pending = None
        if self.control is not None:
            self.stop(self.control, reason)

    async def expire(self, control):
        while control.session.alive():
            await asyncio.sleep(0.5)
        self.stop(control, "expirada")

    async def stream(self, control):
        loop = asyncio.get_running_loop()
        clock = loop.time
        source = capture.StreamCapture()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="screen-capture")
        previous = geometry = None
        last_frame = float("-inf")
        measured_at, frames, total_bytes, capture_ms, send_ms, samples = clock(), 0, 0, 0, 0, 0
        try:
            while control.session.alive():
                started = clock()
                paced_at = time.perf_counter()
                revision, quality = control.revision, control.quality
                monitor, count, region, stamp, jpeg = await loop.run_in_executor(
                    executor, source.frame, control.monitor, quality, previous,
                    geometry is None or geometry[0] != revision or started - last_frame >= 2,
                    control.max_height,
                )
                if not control.session.alive():
                    return
                capture_ms += round((clock() - started) * 1000)
                samples += 1
                if revision != control.revision or quality != control.quality:
                    continue
                control.monitor, control.monitors = monitor, count
                current = (revision, monitor, count, region, self.allow_clipboard, self.allow_files)
                if geometry != current:
                    control.use("screen")
                    geometry = current
                    control.region = region
                    await control.ws.send_json({
                        "t": "info", "w": region["width"], "h": region["height"],
                        "features": {"files": self.allow_files, "view_only": True, "combo": True,
                                     "chat": True, "wol": bool(self.mac), "history": True,
                                     "clipboard": self.allow_clipboard, "high_fps": True,
                                     "stream_resolution": True},
                        **({"mac": self.mac} if self.mac else {}),
                        "monitors": count, "monitor": monitor, "clipboard": self.allow_clipboard,
                        "frame_size": capture.output_size(region["width"], region["height"], control.max_height),
                    })
                if jpeg is not None and revision == control.revision and control.session.alive():
                    sent_at = clock()
                    await control.ws.send_bytes(jpeg)
                    send_ms += round((clock() - sent_at) * 1000)
                    frames += 1
                    total_bytes += len(jpeg)
                    previous, last_frame = stamp, clock()
                if clock() - measured_at >= 60:
                    width, height = capture.output_size(region["width"], region["height"], control.max_height)
                    event("stream_stats", metrics={"width": width, "height": height,
                          "fps": round(frames / (clock() - measured_at)), "frames": frames,
                          "bytes": min(total_bytes, 1_000_000_000), "capture_ms": capture_ms // max(1, samples),
                          "send_ms": send_ms // max(1, frames)})
                    measured_at, frames, total_bytes, capture_ms, send_ms, samples = clock(), 0, 0, 0, 0, 0
                remaining = 1 / control.fps - (time.perf_counter() - paced_at)
                if remaining > 0:
                    # Windows asyncio timers may wake early within its coarse
                    # clock resolution. Sleep off-loop to cap FPS without
                    # spinning or delaying incoming input commands.
                    await loop.run_in_executor(executor, time.sleep, remaining)
                else:
                    await asyncio.sleep(0)
        except (ConnectionError, RuntimeError) as exc:
            event("capture_failed", exc)
            self.stop(control, "conexão ou captura interrompida")
        except Exception as exc:
            event("capture_failed", exc)
            print(f"Falha na captura: {type(exc).__name__}", flush=True)
            self.stop(control, "captura indisponível")
        finally:
            try:
                await asyncio.shield(loop.run_in_executor(executor, source.close))
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

    async def transfer(self, control, message):
        kind = message["t"]
        active = control.session.alive
        try:
            if not self.allow_clipboard:
                raise clipboard.ClipboardError("Área de transferência desativada.")
            if kind == "clip_set":
                size = await clipboard.write_text(message["s"], active)
                reply = {"t": "clip_ok"}
            else:
                value, size = await clipboard.read_text(active)
                reply = {"t": "clip", "s": value}
            print(f"CLIPBOARD {kind}: {size} bytes", flush=True)
        except clipboard.ClipboardError:
            reply = {"t": "error", "op": kind}
        if active():
            await control.ws.send_json(reply)

    async def file(self, control, kind, payload):
        reply = "file_ready"
        try:
            if not self.allow_files:
                raise ValueError
            if kind == "file_begin" and control.upload is None:
                control.upload = Receiver(payload["name"], payload["size"])
                control.use("files")
            elif control.upload is None:
                raise ValueError
            elif kind == "file_block":
                task = asyncio.create_task(asyncio.to_thread(control.upload.block, payload))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
            elif kind == "file_end":
                control.upload.finish(payload["sha256"])
                control.discard_upload()
                reply = "file_ok"
            elif kind == "file_cancel":
                control.discard_upload()
                reply = "file_cancelled"
            else:
                raise ValueError
        except (ValueError, OSError):
            control.discard_upload()
            reply = "file_error"
        if reply != "file_ready":
            print(f"Arquivos: {reply}", flush=True)
        if control.session.alive():
            if reply != "file_error":
                control.session.renew()
            await control.ws.send_json({"t": reply})

    def chat(self, control, text):
        loop = asyncio.get_running_loop()
        answered = False

        async def deliver(value):
            nonlocal answered
            if not answered and self.control is control and control.session.alive():
                answered = True
                await control.ws.send_json({"t": "chat", "s": value})
                control.use("chat_reply")
                return True
            return False

        def reply(value):
            if not valid_chat(value):
                raise ValueError("Mensagem inválida.")
            response = deliver(value)
            try:
                return asyncio.run_coroutine_threadsafe(response, loop)
            except RuntimeError:
                response.close()
                raise ValueError("Sessão encerrada.") from None

        self.notify("chat", {"text": text, "reply": reply})

    async def handle(self, control, message):
        kind = message["t"]
        if not control.session.alive():
            return
        if control.view_only and kind in ("move", "click", "scroll", "key", "text", "combo", "clip_set"):
            return
        if kind.startswith("file_"):
            await self.file(control, kind, message)
            return
        if kind == "ping":
            await control.ws.send_json({"t": "pong", "n": message["n"]})
            return
        if kind == "view_only":
            control.view_only = message["enabled"]
            if control.view_only:
                self.input.release_all()
            await control.ws.send_json(message)
        elif kind == "chat":
            now = time.monotonic()
            if now < control.next_chat:
                await control.ws.send_json({"t": "error", "op": "chat"})
                return
            control.next_chat = now + 1
            self.chat(control, message["s"])
        elif kind in ("quality", "stream"):
            monitor = message["monitor"]
            if monitor > control.monitors:
                await control.ws.send_json({"t": "error", "op": "quality"})
                return
            if monitor != control.monitor:
                control.region = None
                control.monitor = monitor
                control.revision += 1
                self.input.release_all()
            control.quality, control.fps = message["q"], message["fps"]
            height = message.get("height", 0)
            if height != control.max_height:
                control.max_height = height
                control.revision += 1
        elif kind.startswith("clip_"):
            if self.allow_clipboard:
                control.use(kind)
                control.session.renew()
            await self.transfer(control, message)
            return
        elif control.region is not None or kind == "combo":
            try:
                await self.input.apply(message, control.region, control.session.alive)
            except (OSError, ValueError, self.input.keyboard_module.Controller.InvalidCharacterException):
                await control.ws.send_json({"t": "error", "op": kind})
                return
        else:
            return
        control.use(kind)
        control.session.renew()

    async def websocket(self, request):
        with session_lease():
            return await self.control_socket(request)

    async def control_socket(self, request):
        if self.control is not None:
            raise web.HTTPConflict()
        # O token viaja no cabeçalho de subprotocolos, sem entrar na URL.
        headers = request.headers.getall("Sec-WebSocket-Protocol", [])
        offered = [value.strip() for value in headers[0].split(",")] if len(headers) == 1 else []
        if len(offered) != 2 or offered[0] != "markcontrol":
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse(protocols=("markcontrol",), max_msg_size=MAX_MESSAGE + 1,
                                   compress=False, heartbeat=20)
        if not ws.can_prepare(request).ok:
            raise web.HTTPBadRequest()
        session = self.auth.claim(request["peer"], offered[1])
        if session is None:
            raise web.HTTPUnauthorized()
        session.renew()
        control = Control(session, ws, request.transport, self.monitors, quality=self.quality)
        self.control = control
        started = datetime.now(timezone.utc)
        since = time.monotonic()
        tasks = []
        try:
            await ws.prepare(request)
            if not session.alive():
                return ws
            self.notify("session", session.peer)
            event("session_started")
            print(f"Mark Mouse e Teclado Control · SESSÃO ATIVA de {session.peer} — Ctrl+Alt+Shift+Q encerra", flush=True)
            tasks = [asyncio.create_task(self.stream(control)), asyncio.create_task(self.expire(control))]
            async for packet in ws:
                if not session.alive() or packet.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                if packet.type == WSMsgType.BINARY and control.upload is not None:
                    await self.file(control, "file_block", packet.data)
                    continue
                message = decode_message(packet.data) if packet.type == WSMsgType.TEXT else None
                if message is None:
                    self.stop(control, "mensagem inválida")
                    break
                await self.handle(control, message)
        except Exception as exc:
            event("session_failed", exc)
        finally:
            if not session.stopped.is_set():
                session.stopped.set()
                self.input.release_all()
                print(f"SESSAO ENCERRADA de {session.peer}", flush=True)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                control.discard_upload()
            except OSError:
                print("Falha ao remover arquivo parcial.", flush=True)
            try:
                if tasks:
                    await asyncio.to_thread(history.record, self.history_path, started, session.peer,
                                            time.monotonic() - since, control.used)
            except (OSError, ValueError):
                print("Falha ao registrar histórico.", flush=True)
            self.monitors = control.monitors
            self.control = None
            self.notify("session", "")
            event("session_ended")
        return ws


async def run(args, stopping, notify):
    context, fingerprint, public_file = tls_identity(args.host, args.cert_ip)
    monitors = await asyncio.to_thread(capture.monitor_count)
    if not monitors:
        raise RuntimeError("Nenhum monitor encontrado na sessão gráfica.")
    config = Config()
    def warn(message):
        notify("warning", message)

    cfg = config.refresh(warn)
    if cfg.get("review_required"):
        raise ValueError("Revise o PIN e salve em Configurar antes de ativar.")
    agent = Agent(cfg["clipboard"] and not args.no_clipboard, monitors, config, notify, files=cfg["files"])
    agent.quality = cfg["quality"]
    app = web.Application(middlewares=[local_https], client_max_size=1024)
    app.router.add_post("/api/login", agent.login)
    app.router.add_get("/ws", agent.websocket)

    async def asset(request):
        name = request.match_info.get("asset", "") or "index.html"
        return web.FileResponse(STATIC / name)

    app.router.add_get("/", asset)
    app.router.add_get("/{asset:index.html|viewer.js|style.css}", asset)

    async def shutdown(_app):
        agent.stop_active("agente desligado")

    app.on_shutdown.append(shutdown)
    runner = web.AppRunner(app, access_log=None)
    loop = asyncio.get_running_loop()
    listener = agent.input.hotkey(lambda: loop.call_soon_threadsafe(agent.stop_active, "atalho local"))
    discovery = None
    await runner.setup()
    try:
        listener.start()
        if stopping.is_set():
            return
        if not listener.is_alive():
            raise RuntimeError("Atalho local indisponível.")
        await web.TCPSite(runner, args.host, args.port, ssl_context=context).start()
        discovery, _ = await loop.create_datagram_endpoint(
            lambda: Discovery(lambda: dict(name=args.name or cfg["name"], port=args.port,
                                            fingerprint=fingerprint, busy=agent.control is not None,
                                            pin_required=cfg["pin"] is not None)),
            local_addr=("0.0.0.0", PORT), family=socket.AF_INET,
        )
        notify("ready", fingerprint)
        print(f"Mark Mouse e Teclado Control ativo em {args.host}, porta HTTPS {args.port}.", flush=True)
        print(f"SHA-256: {fingerprint}", flush=True)
        print(f"Certificado público: {public_file}", flush=True)
        next_check = loop.time() + 1
        while listener.is_alive() and not stopping.is_set():
            await asyncio.sleep(0.2)
            if loop.time() < next_check:
                continue
            next_check = loop.time() + 1
            updated = config.refresh(warn)
            if (updated["pin"] != cfg["pin"] or not set(cfg["devices"]).issubset(updated["devices"])
                    or updated["files"] != cfg["files"] or updated["clipboard"] != cfg["clipboard"]):
                agent.stop_active("configuração alterada")
            cfg = updated
            agent.allow_files = cfg["files"]
            agent.allow_clipboard = cfg["clipboard"] and not args.no_clipboard
            agent.quality = cfg["quality"]
        if stopping.is_set():
            return
        raise RuntimeError("Atalho local indisponível. O agente foi encerrado.")
    finally:
        listener.stop()
        if discovery is not None:
            discovery.close()
        await runner.cleanup()
