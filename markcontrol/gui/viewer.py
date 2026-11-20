import threading
import time
import tkinter as tk
from queue import Empty
from tkinter import filedialog, ttk

from PIL import ImageTk

from ..client import Client
from ..clipboard import check_text
from ..security import KEYS
from .theme import AMBER, BG, CYAN, FIELD, MUTED, PANEL, TEXT, px
from .widgets import Button, Card, label

FEATURES = {
    "send_file": ("file", "files", "file_transfer", "send_file"),
    "set_view_only": ("view_only", "set_view_only"),
    "send_combo": ("combo", "combos", "send_combo"),
    "send_chat": ("chat", "send_chat"),
}
COMBOS = {
    "Win": ["Meta"], "Win+D": ["Meta", "d"], "Alt+Tab": ["Alt", "Tab"],
    "Ctrl+Shift+Esc": ["Control", "Shift", "Escape"], "PrintScreen": ["PrintScreen"],
    "Bloquear tela": ["Meta", "l"],
}


def feature_method(client, features, method):
    if isinstance(features, dict):
        features = [key for key, value in features.items() if value is True]
    if not isinstance(features, (list, tuple, set)):
        return None
    function = getattr(client, method, None)
    if callable(function) and any(name in features for name in FEATURES[method]):
        return function
    return None


ALIASES = {
    "Alt_L": "Alt", "Alt_R": "Alt", "ISO_Level3_Shift": "AltGraph",
    "BackSpace": "Backspace", "Caps_Lock": "CapsLock", "Control_L": "Control",
    "Control_R": "Control", "Down": "ArrowDown", "Return": "Enter", "KP_Enter": "Enter",
    "Left": "ArrowLeft", "Super_L": "Meta", "Super_R": "Meta", "Win_L": "Meta",
    "Win_R": "Meta", "Meta_L": "Meta", "Meta_R": "Meta", "Next": "PageDown",
    "Prior": "PageUp", "Right": "ArrowRight", "Shift_L": "Shift", "Shift_R": "Shift",
    "ISO_Left_Tab": "Tab", "Up": "ArrowUp", "space": " ",
}


def protocol_key(keysym, char=""):
    key = ALIASES.get(keysym, keysym)
    if len(char) == 1 and char.isprintable() and len(key) == 1:
        key = char
    if key not in KEYS:
        key = char
    if key in KEYS:
        return key.lower() if len(key) == 1 else key
    return None


class Viewer(tk.Toplevel):
    def __init__(self, parent, peer, cfg):
        super().__init__(parent)
        self.peer = peer
        self.title(f"Mark Mouse e Teclado Control · {peer.name} · {peer.ip}")
        self.geometry(f"{px(self, 1040)}x{px(self, 650)}")
        self.minsize(px(self, 620), px(self, 360))
        self.configure(bg=BG)
        self.features = ()
        self.view_only = False
        self.progress = None
        self.transfer = None
        self.tools = {}
        self.fullscreen = False
        self.bind("<F11>", self.toggle_fullscreen)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self.teardown)
        self.status = tk.StringVar(value="Conectando…")
        self.stats = tk.StringVar()
        self.q = tk.StringVar(value=str(cfg["quality"]))
        self.fps = tk.StringVar(value="12")
        self.monitor = tk.StringVar(value="1")
        self.local_clip = cfg["clipboard"]
        self.clip = self.pending_copy = False
        self.keys, self.buttons = {}, set()
        self.photo = self.waiting = None
        self.frames = self.ping = 0
        self.rtt = "—"
        self.tick = time.monotonic()
        self.pos = dict(x=0.5, y=0.5)
        bar = tk.Frame(self, bg=BG, padx=6, pady=px(self, 8))
        bar.pack(fill="x")
        actions = tk.Frame(bar, bg=BG)
        actions.pack(side="right")
        for icon, title, action in (
            ("paste", "Colar no remoto", self.paste), ("copy", "Copiar do remoto", self.copy),
            ("type", "Digitar texto", self.type_text), ("file", "Enviar arquivo", self.send_file),
            ("eye", "Somente ver", self.toggle_view), ("keys", "Teclas especiais", self.special_keys),
            ("chat", "Mensagem", self.chat), ("full", "Tela cheia · F11", self.toggle_fullscreen),
            ("exit", "Desconectar", self.destroy),
        ):
            button = Button(actions, icon, action, 32, tip=title,
                            fill="#45242c" if icon == "exit" else BG,
                            ink="#ffadb6" if icon == "exit" else TEXT)
            button.pack(side="left", padx=1)
            self.tools[icon] = button, title
        selectors = tk.Frame(bar, bg=BG)
        selectors.pack(side="right", padx=3)
        label(bar, peer.name).pack(side="left", fill="x", expand=True)
        label(bar, textvariable=self.stats, color=MUTED, face="TkSmallCaptionFont").pack(side="right")
        for caption, var, choices in (("Qualidade", self.q, sorted({30, cfg["quality"], 65, 90})),
                                    ("FPS", self.fps, (1, 5, 12, 20)), ("Monitor", self.monitor, (1,))):
            group = tk.Frame(selectors, bg=BG)
            group.pack(side="left", padx=2)
            ttk.Label(group, text=caption, font="TkSmallCaptionFont").pack()
            select = ttk.Combobox(group, width=3, textvariable=var, values=choices, state="readonly")
            select.pack(side="left")
            select.bind("<<ComboboxSelected>>", lambda _, v=var: self.quality(v is self.monitor))
            if var is self.monitor:
                self.monitors = select
        self.dialog = Card(self, 210)
        self.prompt = self.dialog.body
        ttk.Label(self, textvariable=self.status).pack(anchor="w")
        self.canvas = tk.Canvas(self, bg="#10151e", highlightthickness=0, takefocus=True)
        self.canvas.pack(expand=True, fill="both")
        self.item = self.canvas.create_image(0, 0)
        self.canvas.bind("<Configure>", self.configure_canvas)
        self.canvas.bind("<Motion>", lambda e: self.pointer(e, "move"))
        self.canvas.bind("<FocusOut>", lambda _: self.release())
        for i, button in enumerate(("left", "middle", "right"), 1):
            for event, direction in (("ButtonPress", "down"), ("ButtonRelease", "up")):
                self.canvas.bind(f"<{event}-{i}>", lambda e, b=button, d=direction: self.pointer(e, "click", b, d))
        self.canvas.bind("<MouseWheel>", lambda e: self.wheel(e, 1 if e.delta > 0 else -1))
        for i, step in ((4, 1), (5, -1)):
            self.canvas.bind(f"<Button-{i}>", lambda e, s=step: self.wheel(e, s))
        self.canvas.bind("<F11>", self.toggle_fullscreen)
        self.canvas.bind("<KeyRelease-F11>", lambda _: "break")
        self.canvas.bind("<KeyPress>", lambda e: self.key(e, True))
        self.canvas.bind("<KeyRelease>", lambda e: self.key(e, False))
        self.connect()
        self.timer = self.after(20, self.poll)

    def toggle_fullscreen(self, _=None):
        self.fullscreen = not self.fullscreen
        self.attributes("-fullscreen", self.fullscreen)
        return "break"

    def refresh_tools(self):
        online = not self.client.closed.is_set()
        available = {"paste": online and self.clip and not self.view_only,
                     "copy": online and self.clip, "type": online and bool(self.photo) and not self.view_only}
        for icon, method in (("file", "send_file"), ("eye", "set_view_only"),
                             ("keys", "send_combo"), ("chat", "send_chat")):
            available[icon] = online and bool(feature_method(self.client, self.features, method))
        for icon, allowed in available.items():
            button, title = self.tools[icon]
            button.tip = title if allowed or icon in ("paste", "copy", "type") else title + " · em breve"
            button.enabled = allowed and not (icon == "keys" and self.view_only)
            if icon == "eye":
                button.fill = "#215f49" if self.view_only else BG
            button.draw()

    def optional(self, method, *args):
        function = feature_method(self.client, self.features, method)
        if function and not self.client.closed.is_set():
            try:
                return function(*args) is not False
            except (OSError, RuntimeError, ValueError) as exc:
                self.status.set(str(exc))
        return False

    def toggle_view(self):
        self.release()
        if self.optional("set_view_only", not self.view_only):
            self.view_only = not self.view_only
            self.refresh_tools()
            self.status.set("Somente ver" if self.view_only else "Conectado")

    def special_keys(self):
        if self.view_only:
            return
        menu = tk.Menu(self, tearoff=False)
        for title, keys in COMBOS.items():
            menu.add_command(label=title, command=lambda k=keys: self.optional("send_combo", list(k)))
        button = self.tools["keys"][0]
        menu.bind("<Unmap>", lambda _: menu.after_idle(menu.destroy))
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty()+button.winfo_height())

    def chat(self):
        def submit(value):
            if not value.strip() or len(value) > 500 or "\x00" in value:
                self.status.set("Use entre 1 e 500 caracteres válidos.")
            elif self.optional("send_chat", value):
                self.clear_prompt()
        self.entry("Enviar mensagem", submit)

    def send_file(self):
        client = self.client
        method = feature_method(client, self.features, "send_file")
        if not method or client.closed.is_set() or self.transfer and self.transfer.is_alive():
            return
        path = filedialog.askopenfilename(parent=self, title="Enviar arquivo")
        if not path or client.closed.is_set():
            return

        def progress(sent, total=None):
            if total:
                self.progress = client, f"Enviando arquivo · {sent * 100 / total:.0f}%"
            else:
                self.progress = client, f"Enviando arquivo · {sent}"

        def send():
            try:
                method(path, progress)
            except Exception as exc:
                self.progress = client, f"Falha no envio: {exc}"

        self.status.set("Enviando arquivo…")
        self.transfer = threading.Thread(target=send, daemon=True)
        self.transfer.start()

    def teardown(self, event):
        if event.widget is self:
            self.client.close()
            self.after_cancel(self.timer)

    def clear_prompt(self):
        self.dialog.place_forget()
        for widget in self.prompt.winfo_children():
            widget.destroy()

    def connect(self, pin=None, retrust=False):
        self.clear_prompt()
        self.status.set("Conectando…")
        self.features = ()
        self.view_only = False
        self.online = False
        self.client = Client(self.peer)
        self.refresh_tools()
        self.client.target = (self.canvas.winfo_width(), self.canvas.winfo_height())
        self.client.start(pin, retrust)

    def card(self, title, detail, warning=False):
        self.clear_prompt()
        self.dialog.outline = AMBER if warning else "#31576b"
        self.dialog.place(relx=.5, rely=.5, anchor="center", width=px(self, 435), height=px(self, 214))
        tk.Misc.lift(self.dialog)
        label(self.prompt, title, face="TkHeadingFont", color=AMBER if warning else TEXT).pack(anchor="w")
        label(self.prompt, detail, color=MUTED, justify="left", wraplength=px(self, 395)).pack(anchor="w", pady=5)

    def entry(self, caption, callback, secret=False):
        self.card("Digite o PIN" if secret else caption,
                  f"O PC {self.peer.name[:48]} exige um PIN para conectar." if secret else self.peer.name)
        if secret:
            label(self.prompt, "PIN de 8 dígitos").pack(anchor="w")
        field = ttk.Entry(self.prompt, show="•" if secret else "")
        field.pack(fill="x")
        field.focus_set()

        def submit(*_):
            value = field.get()
            if secret and not (len(value) == 8 and value.isascii() and value.isdigit()):
                self.status.set("Informe oito dígitos para o PIN.")
                return
            field.delete(0, "end")
            callback(value)

        field.bind("<Return>", submit)
        actions = tk.Frame(self.prompt, bg=PANEL)
        actions.pack(side="bottom", anchor="e")
        Button(actions, "Cancelar", self.destroy if secret else self.clear_prompt).pack(side="left", padx=4)
        Button(actions, caption, submit, 135, fill=CYAN, ink=FIELD).pack(side="left")

    def identity(self, peer):
        self.card("Este PC mudou de identidade",
                  f"Compare o código abaixo com o exibido no PC {peer.name[:48]} antes de continuar.", True)
        label(self.prompt, "SHA-256  " + peer.fingerprint, wraplength=px(self, 395),
              justify="left", face="TkSmallCaptionFont").pack(fill="x")
        actions = tk.Frame(self.prompt, bg=PANEL)
        actions.pack(side="bottom", anchor="e")
        Button(actions, "Cancelar", self.destroy).pack(side="left", padx=4)
        Button(actions, "Confiar de novo", lambda: self.connect(retrust=True), 130,
               fill=AMBER, ink=FIELD).pack(side="left")

    def type_text(self):
        if self.photo and not self.view_only:
            def submit(value):
                if self.photo and not self.view_only and len(value) <= 500 and "\x00" not in value:
                    self.client.send(dict(t="text", s=value))
                    self.clear_prompt()
                    self.canvas.focus_set()
                else:
                    self.status.set("Use até 500 caracteres válidos.")
            self.entry("Enviar texto", submit)

    def paste(self):
        if self.clip and not self.view_only:
            try:
                value = self.clipboard_get()
                check_text(value)
                self.client.send(dict(t="clip_set", s=value))
            except (ValueError, tk.TclError):
                self.status.set("Clipboard indisponível ou acima de 100 KiB.")

    def copy(self):
        if self.clip and not self.pending_copy:
            self.pending_copy = True
            self.client.send(dict(t="clip_get"))

    def quality(self, switching=False):
        if switching:
            self.release()
            self.waiting = int(self.monitor.get())
            self.blank()
        self.client.send(dict(t="quality", q=int(self.q.get()), fps=int(self.fps.get()),
                              monitor=int(self.monitor.get())))

    def key(self, e, down):
        if not self.photo or getattr(self, "view_only", False):
            return "break"
        if down and e.keycode not in self.keys:
            if e.keysym.startswith("dead_") or e.keysym == "Multi_key":
                return "break"
            if e.char and e.char.isprintable() and e.char not in KEYS:
                if len(e.char) <= 500:
                    self.client.send(dict(t="text", s=e.char))
                    self.keys[e.keycode] = None
                return "break"
            k = protocol_key(e.keysym, e.char)
            if e.state & 5 == 5 and k in ("v", "c"):
                (self.paste if k == "v" else self.copy)()
            elif k:
                if k not in self.keys.values():
                    self.client.send(dict(t="key", k=k, d="down"))
                self.keys[e.keycode] = k
        elif not down:
            k = self.keys.pop(e.keycode, None)
            if k and k not in self.keys.values():
                self.client.send(dict(t="key", k=k, d="up"))
        return "break"

    def pointer(self, e, kind, b=None, d=None):
        if not self.photo or getattr(self, "view_only", False):
            return
        left, top, right, bottom = self.canvas.bbox(self.item)
        if not self.buttons and not (left <= e.x < right and top <= e.y < bottom):
            return
        self.pos = dict(x=min(1, max(0, (e.x-left)/max(1, right-left-1))),
                        y=min(1, max(0, (e.y-top)/max(1, bottom-top-1))))
        msg = dict(t=kind, **self.pos)
        if b:
            self.canvas.focus_set()
            (self.buttons.add if d == "down" else self.buttons.discard)(b)
            msg.update(b=b, d=d)
        self.client.send(msg)
        return True

    def wheel(self, e, step):
        if self.pointer(e, "move"):
            horizontal = bool(e.state & 1)
            self.client.send(dict(t="scroll", dx=step if horizontal else 0, dy=0 if horizontal else step))

    def release(self):
        for k in set(self.keys.values()) - {None}:
            self.client.send(dict(t="key", k=k, d="up"))
        for b in self.buttons:
            self.client.send(dict(t="click", b=b, d="up", **self.pos))
        self.keys.clear()
        self.buttons.clear()

    def blank(self):
        self.photo = None
        self.canvas.itemconfigure(self.item, image="")

    def configure_canvas(self, event):
        self.client.target = (max(1, event.width), max(1, event.height))
        self.canvas.coords(self.item, event.width//2, event.height//2)

    def draw(self, frame):
        self.photo = ImageTk.PhotoImage(frame, master=self)
        self.canvas.itemconfigure(self.item, image=self.photo)

    def message(self, m):
        kind = m["t"]
        if kind == "info":
            count, current = m["monitors"], m["monitor"]
            if type(count) is not int or not 1 <= count <= 256 or type(current) is not int or not 1 <= current <= count:
                raise ValueError("Monitor inválido.")
            if self.waiting and self.waiting <= count and self.waiting != current:
                return
            self.release()
            self.blank()
            self.waiting = None
            self.monitors.configure(values=tuple(range(1, count+1)))
            self.monitor.set(str(current))
            self.clip = self.local_clip and m["clipboard"] is True
            self.online = True
            self.features = m.get("features", ())
            self.refresh_tools()
            self.quality()
            self.status.set("Conectado")
            self.canvas.focus_set()
        elif kind == "clip" and self.pending_copy and self.clip:
            self.pending_copy = False
            check_text(m["s"])
            self.clipboard_clear()
            self.clipboard_append(m["s"])
            self.status.set("Texto copiado do remoto.")
        elif kind == "chat" and isinstance(m.get("s"), str):
            self.status.set(m["s"][:500])
        elif kind == "clip_ok":
            self.status.set("Texto disponível no remoto.")
        elif kind == "pong" and self.ping == m["n"]:
            self.rtt = round(1000*(time.monotonic()-self.tick))
        elif kind == "error":
            self.pending_copy = False
            self.status.set("O agente recusou a operação.")
            if m.get("op") == "quality" and self.waiting:
                self.client.close()
                self.status.set("Monitor indisponível. Reconecte ao PC.")

    def poll(self):
        try:
            for _ in range(16):
                kind, value = self.client.events.get_nowait()
                if kind == "frame" and not self.waiting:
                    first = self.photo is None
                    self.draw(value)
                    if first:
                        self.refresh_tools()
                    self.frames += 1
                elif kind == "message":
                    self.message(value)
                elif kind == "pin":
                    self.status.set(value)
                    self.entry("Entrar com PIN", self.connect, True)
                elif kind == "changed":
                    self.peer = value
                    self.status.set("Este PC mudou de identidade. SHA-256: " + value.fingerprint)
                    self.identity(value)
                elif kind == "chat":
                    self.status.set(str(value)[:500])
                elif kind in ("end", "error"):
                    self.blank()
                    self.clip = False
                    self.status.set(value or "Sessão encerrada.")
        except Empty:
            pass
        except (ValueError, TypeError, KeyError, OSError, tk.TclError):
            self.client.close()
            self.blank()
            self.status.set("Resposta inválida ou exibição indisponível.")
        if self.photo and self.client.closed.is_set() and self.client.events.empty():
            self.blank()
            self.clip = False
            self.status.set("Sessão encerrada.")
        if self.client.closed.is_set() and self.online:
            self.online = False
            self.features = ()
            self.refresh_tools()
        if self.progress:
            client, message = self.progress
            self.progress = None
            if client is self.client and not client.closed.is_set():
                self.status.set(message)
        now = time.monotonic()
        if self.photo and now-self.tick >= 1:
            self.stats.set(f"{round(self.frames/(now-self.tick))} FPS · {self.rtt} ms")
            self.tick, self.frames = now, 0
            self.ping = (self.ping+1) % 2**31
            self.client.send(dict(t="ping", n=self.ping))
        self.timer = self.after(20, self.poll)
