import threading
import time
import tkinter as tk
from queue import Empty
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from ..client import Client
from ..diagnostics import event
from ..clipboard import check_text
from ..security import KEYS
from .icons import install_icon
from .theme import AMBER, BG, CYAN, FIELD, MUTED, PANEL, TEXT, TITLE, WINDOW_TITLE, px
from .widgets import Button, Card, Selector, entry_field, icon, label

FEATURES = {
    "send_file": ("file", "files", "file_transfer", "send_file"),
    "set_view_only": ("view_only", "set_view_only"),
    "send_combo": ("combo", "combos", "send_combo"),
    "send_chat": ("chat", "send_chat"),
}
# Nomes exatamente iguais a security.COMBOS: o cliente recusa qualquer outra grafia.
COMBOS = {
    "Win": ["Win"], "Win+D (área de trabalho)": ["Win", "D"], "Alt+Tab": ["Alt", "Tab"],
    "Alt+F4 (fechar janela)": ["Alt", "F4"], "Ctrl+Shift+Esc (Gerenciador de Tarefas)": ["Ctrl", "Shift", "Esc"],
    "PrintScreen": ["PrintScreen"], "Bloquear tela (Win+L)": ["Win", "L"],
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
    # Pontuação: com Ctrl o Tk entrega um char de controle (ou vazio), então o keysym decide.
    "minus": "-", "equal": "=", "slash": "/", "comma": ",", "period": ".",
    "semicolon": ";", "apostrophe": "'", "bracketleft": "[", "bracketright": "]",
    "backslash": "\\", "grave": "`", "plus": "+",
    # Teclado numérico (com e sem NumLock).
    "KP_Add": "+", "KP_Subtract": "-", "KP_Multiply": "*", "KP_Divide": "/", "KP_Decimal": ".",
    **{f"KP_{i}": str(i) for i in range(10)},
    "KP_Home": "Home", "KP_End": "End", "KP_Prior": "PageUp", "KP_Next": "PageDown",
    "KP_Up": "ArrowUp", "KP_Down": "ArrowDown", "KP_Left": "ArrowLeft", "KP_Right": "ArrowRight",
    "KP_Insert": "Insert", "KP_Delete": "Delete",
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


FILE_RESULTS = {
    "file_ok": "Arquivo enviado. No PC remoto: pasta Downloads › Mark Mouse e Teclado Control.",
    "file_error": "O PC remoto recusou o arquivo (envio desativado, sem espaço ou nome inválido).",
    "file_cancelled": "Envio de arquivo cancelado.",
}
ADMIN_WINDOW_NOTICE = ("Janela de administrador no PC remoto: o Windows bloqueou o clique. "
                       "Abra o app do PC remoto como administrador.")

class Viewer(tk.Toplevel):
    def __init__(self, parent, peer, cfg):
        super().__init__(parent)
        install_icon(self)
        self.peer = peer
        self.title(f"{WINDOW_TITLE} · {peer.name} · {peer.ip}")
        width = min(px(self, 1440), self.winfo_screenwidth() - px(self, 48))
        height = min(px(self, 900), self.winfo_screenheight() - px(self, 100))
        self.geometry(f"{max(320, width)}x{max(240, height)}")
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
        self.fps = tk.StringVar(value="30")
        self.resolution = tk.StringVar(value="Automática")
        self.source_size = self.stream_size = None
        self.resize_timer = None
        self.scale_mode = tk.StringVar(value="Ajustar à janela")
        self.monitor = tk.StringVar(value="1")
        self.local_clip = cfg["clipboard"]
        self.clip = self.pending_copy = False
        self.keys, self.buttons = {}, set()
        self.photo = self.waiting = None
        self.frames = self.ping = 0
        self.rtt = "—"
        self.tick = time.monotonic()
        self.last_diagnostic = self.tick
        self.pos = dict(x=0.5, y=0.5)
        u = lambda n: px(self, n)
        shell = Card(self, 650, BG, "#2c4052", pad=(4, 3), radius=14, shadow=True)
        shell.configure(bg="#0f1720")
        shell.pack(fill="both", expand=True)
        screen = shell.body
        bar_color = "#162331"
        bar = tk.Frame(screen, bg=bar_color, padx=u(9), pady=u(8))
        bar.pack(fill="x")
        bar.columnconfigure(0, weight=1)
        pc = tk.Frame(bar, bg=bar_color)
        pc.grid(row=0, column=0, sticky="w")
        icon(pc, "monitor", "#91d8e7").pack(side="left", padx=(0, u(8)))
        label(pc, peer.name[:28], face="MarkStrong").pack(side="left")
        tk.Frame(pc, bg="#3a4c5e", width=1, height=u(17)).pack(side="left", padx=u(11))
        label(pc, textvariable=self.stats, color=MUTED, face="MarkSmall").pack(side="left")
        Selector(pc, "Escala", self.scale_mode, ("Ajustar à janela", "Tamanho real"),
                 self.change_scale, 180).pack(side="left", padx=u(4))
        actions = tk.Frame(bar, bg=bar_color, padx=u(3))
        actions.grid(row=0, column=2, sticky="e")
        tk.Frame(actions, bg="#3a4c5e", width=1, height=u(34)).pack(side="left", padx=(0, u(10)))
        for symbol, title, action in (
            ("paste", "Colar no remoto", self.paste), ("copy", "Copiar do remoto", self.copy),
            ("type", "Digitar texto", self.type_text), ("file", "Enviar arquivo", self.send_file),
            ("eye", "Somente ver", self.toggle_view), ("keys", "Teclas especiais", self.special_keys),
            ("chat", "Mensagem", self.chat), ("full", "Tela cheia · F11", self.toggle_fullscreen),
            ("exit", "Desconectar", self.destroy),
        ):
            button = Button(actions, symbol, action, 34, tip=title,
                            fill="#45242c" if symbol == "exit" else bar_color,
                            edge="#774048" if symbol == "exit" else bar_color,
                            ink="#ffadb6" if symbol == "exit" else TEXT)
            button.pack(side="left", padx=(u(5), 0))
            self.tools[symbol] = button, title
        selectors = tk.Frame(bar, bg=bar_color)
        selectors.grid(row=0, column=1, sticky="e", padx=u(8))
        for caption, var, choices, width in (
            ("Qualidade", self.q, sorted({30, cfg["quality"], 65, 85, 90, 95}), 116),
            ("FPS", self.fps, (5, 15, 20, 30, 45, 60), 78), ("Monitor", self.monitor, (1,), 99),
            ("Resolução", self.resolution, ("Automática", "Original", "720p", "1080p", "1440p", "2160p"), 166),
        ):
            select = Selector(selectors, caption, var, choices, lambda v=var: self.quality(v is self.monitor), width)
            select.pack(side="left", padx=(u(7), 0))
            if var is self.monitor:
                self.monitors = select
            if var is self.fps:
                self.fps_selector = select

        def fit(event):
            narrow = event.width < pc.winfo_reqwidth()+selectors.winfo_reqwidth()+actions.winfo_reqwidth()+u(42)
            pc.grid_configure(columnspan=2 if narrow else 1)
            selectors.grid_configure(row=int(narrow), column=0 if narrow else 1, sticky="w" if narrow else "e")
            actions.grid_configure(row=int(narrow), column=2)
        bar.bind("<Configure>", fit)
        tk.Frame(screen, bg="#2c4052", height=1).pack(fill="x")
        self.dialog = None
        self.canvas = tk.Canvas(screen, bg="#10151e", bd=0, highlightthickness=0, takefocus=True)
        self.canvas.pack(expand=True, fill="both")
        self.horizontal = ttk.Scrollbar(screen, orient="horizontal", command=self.canvas.xview)
        self.vertical = ttk.Scrollbar(screen, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.horizontal.set, yscrollcommand=self.vertical.set)
        notice = label(self.canvas, textvariable=self.status, color=MUTED, face="MarkSmall",
                       wraplength=u(510), justify="left")
        def status_changed(*_):
            if self.status.get() in ("", "Conectado"):
                notice.place_forget()
            else:
                notice.place(x=u(8), y=u(8))
        self.status.trace_add("write", status_changed)
        status_changed()
        self.item = self.canvas.create_image(0, 0)
        self.canvas.bind("<Configure>", self.configure_canvas)
        self.canvas.bind("<Motion>", lambda e: self.pointer(e, "move"))
        self.canvas.bind("<FocusOut>", lambda _: self.release())
        for i, button in enumerate(("left", "middle", "right"), 1):
            for event, direction in (("ButtonPress", "down"), ("ButtonRelease", "up")):
                self.canvas.bind(f"<{event}-{i}>", lambda e, b=button, d=direction: self.pointer(e, "click", b, d))
        self.canvas.bind("<MouseWheel>", lambda e: self.wheel(e, delta=e.delta))
        for i, step in ((4, 1), (5, -1)):
            self.canvas.bind(f"<Button-{i}>", lambda e, s=step: self.wheel(e, s))
        for i, step in ((6, -1), (7, 1)):  # X11: rolagem horizontal (esquerda/direita)
            try:
                self.canvas.bind(f"<Button-{i}>", lambda e, s=step: self.wheel(e, s, horizontal=True))
            except tk.TclError:  # Tk 8.6 só aceita botões 1-5
                break
        self.canvas.bind("<F11>", self.toggle_fullscreen)
        self.canvas.bind("<KeyRelease-F11>", lambda _: "break")
        self.canvas.bind("<KeyPress>", lambda e: self.key(e, True))
        self.canvas.bind("<KeyRelease>", lambda e: self.key(e, False))
        self.connect()
        self.timer = self.after(8, self.poll)

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
            if icon in ("file", "eye", "keys", "chat"):
                if allowed:
                    button.pack(before=self.tools["full"][0], side="left", padx=(px(self, 5), 0))
                else:
                    button.pack_forget()
            if icon == "eye":
                button.fill = "#215f49" if self.view_only else "#162331"
            button.draw()

    def optional(self, method, *args):
        function = feature_method(self.client, self.features, method)
        if function and not self.client.closed.is_set():
            try:
                return function(*args) is not False
            except (OSError, RuntimeError, ValueError) as exc:
                event("remote_operation_failed", exc)
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
        if not method or client.closed.is_set():
            return
        if getattr(self, "uploading", False):
            if messagebox.askyesno("Enviar arquivo", "Cancelar o envio em andamento?", parent=self):
                cancel = getattr(client, "cancel_file", None)
                if callable(cancel):
                    cancel()
                self.status.set("Cancelando envio…")
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
                self.uploading = False
                event("file_transfer_failed", exc)
                self.progress = client, f"Falha no envio: {exc}"

        self.status.set("Enviando arquivo… clique de novo em Enviar arquivo para cancelar.")
        self.uploading = True
        self.transfer = threading.Thread(target=send, daemon=True)
        self.transfer.start()

    def teardown(self, event):
        if event.widget is self:
            self.client.close()
            self.after_cancel(self.timer)

    def clear_prompt(self):
        if self.dialog is not None:
            self.dialog.destroy()
            self.dialog = None

    def connect(self, pin=None, retrust=False):
        self.clear_prompt()
        self.status.set("Conectando…")
        self.features = ()
        self.view_only = False
        self.online = False
        self.pending_copy = False
        self.client = Client(self.peer)
        self.refresh_tools()
        self.configure_canvas()
        self.client.start(pin, retrust)

    def card(self, title, detail, warning=False, secret=False):
        self.clear_prompt()
        dialog = self.dialog = tk.Toplevel(self)
        dialog.withdraw()
        install_icon(dialog)
        dialog.title(f"{TITLE} · {title}")
        if self.winfo_viewable():
            dialog.transient(self)
        dialog.protocol("WM_DELETE_WINDOW", self.destroy if warning or secret else self.clear_prompt)
        dialog.resizable(False, False)
        width = 480 if warning else 360
        card = Card(dialog, 180, BG, "#735936" if warning else "#2c4052",
                    pad=(17, 17), radius=14, shadow=True)
        card.configure(width=px(self, width), bg="#0f1720")
        card.pack(fill="both", expand=True)
        self.prompt = card.body
        head = tk.Frame(self.prompt, bg=BG)
        head.pack(fill="x", pady=(0, px(self, 12)))
        icon(head, "shield" if warning else "lock" if secret else "type",
             AMBER if warning else "#8adcf0", plate=37,
             fill="#3c3022" if warning else None, edge="#695232" if warning else None).pack(
                 side="left", anchor="n", padx=(0, px(self, 11)))
        words = tk.Frame(head, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        label(words, title, face="MarkDialog").pack(anchor="w")
        label(words, detail, face="MarkDetail", color=MUTED, justify="left",
              wraplength=px(self, width-84)).pack(anchor="w", pady=(px(self, 4), 0))

    def show_card(self):
        dialog = self.dialog
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = self.prompt.winfo_reqheight()+px(self, 41)
        x = max(0, min(self.winfo_screenwidth()-width, self.winfo_rootx()+(self.winfo_width()-width)//2))
        y = max(0, min(self.winfo_screenheight()-height, self.winfo_rooty()+(self.winfo_height()-height)//2))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()
        dialog.lift()

    def entry(self, caption, callback, secret=False):
        self.card("Digite o PIN" if secret else caption,
                  f"O PC {self.peer.name[:48]} exige um PIN para conectar." if secret else self.peer.name,
                  secret=secret)
        if secret:
            label(self.prompt, "PIN de 8 dígitos", face="MarkStrong").pack(anchor="w", pady=(0, px(self, 6)))
        field = entry_field(self.prompt, show="•" if secret else "",
                            placeholder="•  •  •  •  •  •  •  •" if secret else "")
        error = label(self.prompt, color=AMBER, face="MarkSmall")

        def submit(*_):
            value = field.get()
            if secret and not (len(value) == 8 and value.isascii() and value.isdigit()):
                error.configure(text="Informe oito dígitos para o PIN.")
                error.pack(before=actions, anchor="w")
                self.show_card()
                return
            field.delete(0, "end")
            callback(value)

        field.bind("<Return>", submit)
        actions = tk.Frame(self.prompt, bg=BG)
        actions.pack(anchor="e", pady=(px(self, 12), 0))
        Button(actions, "Cancelar", self.destroy if secret else self.clear_prompt, 82, fill=BG).pack(
            side="left", padx=(0, px(self, 8)))
        Button(actions, caption, submit, 120, fill=CYAN, ink="#08232e").pack(side="left")
        self.show_card()
        field.focus_set()

    def identity(self, peer):
        self.card("Este PC mudou de identidade",
                  f"Compare o código abaixo com o exibido no PC {peer.name[:48]} antes de continuar.", True)
        digest = Card(self.prompt, 45, FIELD, "#354653", pad=(10, 4), radius=10)
        digest.pack(fill="x")
        label(digest.body, "SHA-256", face="MarkBadge", color=MUTED).pack(side="left", padx=(0, px(self, 8)))
        label(digest.body, peer.fingerprint.replace(":", ""), wraplength=px(self, 361),
              color="#dce6ed", justify="left", face="MarkDigest").pack(side="left")
        actions = tk.Frame(self.prompt, bg=BG)
        actions.pack(anchor="e", pady=(px(self, 12), 0))
        Button(actions, "Cancelar", self.destroy, 82, fill=BG).pack(side="left", padx=(0, px(self, 8)))
        Button(actions, "Confiar de novo", lambda: self.connect(retrust=True), 124,
               fill="#ffda85", ink="#33240f").pack(side="left")
        self.show_card()

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

    def rich_clipboard(self):
        caps = getattr(self, "features", {})
        return isinstance(caps, dict) and caps.get("clipboard_rich") is True

    def paste(self):
        if self.clip and not self.view_only:
            if Viewer.rich_clipboard(self):
                if self.client.clipboard_from_remote() is True:
                    # O conteúdo veio do próprio remoto: só colar lá, sem reenviar.
                    for key, direction in (("Control", "down"), ("v", "down"), ("v", "up"), ("Control", "up")):
                        self.client.send(dict(t="key", k=key, d=direction))
                    self.status.set("Colado no remoto.")
                    return
                try:
                    accepted = self.client.paste_clipboard(paste_after=True)
                    self.status.set("Enviando área de transferência…" if accepted is not False
                                    else "Aguarde a transferência atual terminar.")
                except (ValueError, OSError):
                    self.status.set("Não foi possível enviar a área de transferência.")
                return
            try:
                value = self.clipboard_get()
                check_text(value)
                self.client.send(dict(t="clip_set", s=value))
            except (ValueError, tk.TclError):
                self.status.set("Clipboard indisponível ou acima de 100 KiB.")

    def copy(self, *, copy_first=False, cut=False):
        if self.clip and not self.pending_copy:
            self.pending_copy = True
            if Viewer.rich_clipboard(self):
                try:
                    accepted = (self.client.copy_clipboard(copy_first=copy_first, cut=True) if cut
                                else self.client.copy_clipboard(copy_first=copy_first))
                    if accepted is False:
                        self.pending_copy = False
                    self.status.set("Recebendo área de transferência…" if accepted is not False
                                    else "Aguarde a transferência atual terminar.")
                except (ValueError, OSError):
                    self.pending_copy = False
                    self.status.set("Não foi possível receber a área de transferência.")
                return
            self.client.send(dict(t="clip_get"))

    def clipboard_result(self, kind, value):
        self.pending_copy = False
        if kind == "clipboard_error":
            self.status.set(str(value))
            return
        direction = value.get("direction") if isinstance(value, dict) else None
        content = value.get("kind") if isinstance(value, dict) else None
        if direction == "copy":
            label = {"text": "Texto disponível", "image": "Imagem disponível", "files": "Arquivos disponíveis"}.get(content, "Conteúdo disponível")
            self.status.set(label + " para colar neste PC.")
        else:
            label = {"text": "Texto enviado", "image": "Imagem enviada", "files": "Arquivos enviados"}.get(content, "Conteúdo enviado")
            self.status.set(label + " ao outro PC.")

    def quality(self, switching=False):
        if switching:
            self.release()
            self.waiting = int(self.monitor.get())
            self.blank()
        caps = self.features if isinstance(self.features, dict) else {}
        fps = min(int(self.fps.get()), 60 if caps.get("high_fps") else 20)
        message = dict(t="quality", q=int(self.q.get()), fps=fps, monitor=int(self.monitor.get()))
        if caps.get("stream_resolution"):
            choice = self.resolution.get()
            height = (max(240, min(2160, self.canvas.winfo_height()))
                      if choice == "Automática" and self.scale_mode.get() != "Tamanho real"
                      else 0 if choice == "Automática"
                      else 0 if choice == "Original" else int(choice[:-1]))
            message.update(t="stream", height=height)
        self.client.send(message)

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
            if (e.state & 4 and k in ("v", "c", "x") and self.clip and Viewer.rich_clipboard(self)
                    and not (k == "v" and self.client.clipboard_from_remote() is True)
                    and not (k == "x" and getattr(self.client, "clipboard_cut", False) is not True)):
                self.keys[e.keycode] = None
                if k == "v":
                    self.paste()
                else:
                    self.copy(copy_first=True, cut=k == "x")
            elif e.state & 5 == 5 and k in ("v", "c"):
                (self.paste if k == "v" else self.copy)()
            elif k:
                if k not in self.keys.values():
                    self.client.send(dict(t="key", k=k, d="down"))
                self.keys[e.keycode] = k
        elif down:
            # Tecla segurada: o Windows repete KeyPress sem KeyRelease; repete no PC remoto.
            k = self.keys[e.keycode]
            if k:
                self.client.send(dict(t="key", k=k, d="down"))
            elif (e.char and e.char.isprintable() and e.char not in KEYS
                    and not e.keysym.startswith("dead_") and len(e.char) <= 500):
                self.client.send(dict(t="text", s=e.char))
        elif not down:
            k = self.keys.pop(e.keycode, None)
            if k and k not in self.keys.values():
                self.client.send(dict(t="key", k=k, d="up"))
        return "break"

    def pointer(self, e, kind, b=None, d=None):
        if not self.photo or getattr(self, "view_only", False):
            return
        left, top, right, bottom = self.canvas.bbox(self.item)
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        if not self.buttons and not (left <= x < right and top <= y < bottom):
            return
        self.pos = dict(x=min(1, max(0, (x-left)/max(1, right-left-1))),
                        y=min(1, max(0, (y-top)/max(1, bottom-top-1))))
        msg = dict(t=kind, **self.pos)
        if b:
            self.canvas.focus_set()
            (self.buttons.add if d == "down" else self.buttons.discard)(b)
            msg.update(b=b, d=d)
        self.client.send(msg)
        return True

    def wheel(self, e, step=0, delta=None, horizontal=False):
        """Rola no PC remoto. No pynput, dy > 0 sobe e dx > 0 vai para a direita."""
        if not self.pointer(e, "move"):
            return
        shifted = not horizontal and bool(e.state & 1)
        if delta is not None:
            # Windows/macOS: acumula e envia um entalhe a cada 120 (touchpads mandam frações).
            import sys
            unit = 1 if sys.platform == "darwin" and tk.TkVersion < 8.7 else 120
            axis = "x" if shifted else "y"
            acc = getattr(self, "wheel_acc", None)
            if not isinstance(acc, dict):
                acc = self.wheel_acc = {"x": 0, "y": 0}
            if acc[axis] * delta < 0:  # mudou de sentido: descarta a sobra
                acc[axis] = 0
            acc[axis] += delta
            step = int(acc[axis] / unit)
            acc[axis] -= step * unit
        if shifted:
            step = -step  # Shift+roda para cima = esquerda
        while step:
            part = max(-20, min(20, step))
            step -= part
            horizontal_scroll = horizontal or shifted
            self.client.send(dict(t="scroll", dx=part if horizontal_scroll else 0,
                                  dy=0 if horizontal_scroll else part))

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

    def change_scale(self):
        if self.scale_mode.get() == "Tamanho real":
            self.horizontal.pack(side="bottom", fill="x", before=self.canvas)
            self.vertical.pack(side="right", fill="y", before=self.canvas)
        else:
            self.horizontal.pack_forget()
            self.vertical.pack_forget()
        self.configure_canvas()
        self.quality()
        self.canvas.focus_set()

    def configure_canvas(self, event=None):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if self.scale_mode.get() == "Tamanho real":
            self.client.target = None
            self.canvas.itemconfigure(self.item, anchor="nw")
            self.canvas.coords(self.item, 0, 0)
            self.canvas.configure(scrollregion=self.canvas.bbox(self.item))
        else:
            self.client.target = (w, h)
            self.canvas.itemconfigure(self.item, anchor="center")
            self.canvas.coords(self.item, w//2, h//2)
            self.canvas.configure(scrollregion=(0, 0, w, h))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        if event is not None and self.resolution.get() == "Automática":
            if self.resize_timer is not None:
                self.after_cancel(self.resize_timer)
            self.resize_timer = self.after(200, self.quality)

    def draw(self, frame):
        self.photo = ImageTk.PhotoImage(frame, master=self)
        self.canvas.itemconfigure(self.item, image=self.photo)
        if self.client.target is None:
            self.canvas.configure(scrollregion=(0, 0, frame.width, frame.height))

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
            self.monitors.choices = tuple(range(1, count+1))
            self.monitor.set(str(current))
            self.clip = self.local_clip and m["clipboard"] is True
            self.online = True
            self.features = m.get("features", ())
            self.source_size = (m["w"], m["h"])
            self.stream_size = m.get("frame_size", self.source_size)
            self.fps_selector.choices = (5, 15, 20, 30, 45, 60) if isinstance(self.features, dict) and self.features.get("high_fps") else (5, 15, 20)
            self.refresh_tools()
            self.quality()
            self.status.set("Conectado")
            event("viewer_connected")
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
        elif kind == "notice" and m.get("code") == "admin_window":
            self.status.set(ADMIN_WINDOW_NOTICE)
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
                elif kind in ("clipboard_ok", "clipboard_error"):
                    self.clipboard_result(kind, value)
                elif kind in FILE_RESULTS:
                    self.uploading = False
                    self.progress = None
                    self.status.set(FILE_RESULTS[kind])
                elif kind in ("end", "error"):
                    self.blank()
                    self.clip = False
                    self.pending_copy = False
                    self.status.set(value or "Sessão encerrada.")
        except Empty:
            pass
        except (ValueError, TypeError, KeyError, OSError, tk.TclError):
            self.client.close()
            self.blank()
            self.status.set("Resposta inválida ou exibição indisponível.")
        if not self.waiting and not self.client.closed.is_set():
            try:
                latest = self.client.frames.get_nowait()
                first = self.photo is None
                self.draw(latest)
                if first:
                    self.refresh_tools()
                self.frames += 1
            except Empty:
                pass
            except (ValueError, TypeError, OSError, tk.TclError) as exc:
                event("remote_operation_failed", exc)
                self.client.close()
                self.blank()
                self.status.set("Exibição indisponível. Reconecte ao PC.")
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
            measured_fps = round(self.frames / (now-self.tick))
            if now - self.last_diagnostic >= 60:
                size = self.stream_size or (0, 0)
                event("viewer_stats", metrics={"width": size[0], "height": size[1], "fps": measured_fps,
                      "decode_ms": self.client.decode_ms, "rtt_ms": int(self.rtt) if str(self.rtt).isdigit() else 0})
                self.last_diagnostic = now
            resolution = f"{self.stream_size[0]}×{self.stream_size[1]} · " if self.stream_size else ""
            self.stats.set(f"{resolution}{round(self.frames/(now-self.tick))} FPS · {self.rtt} ms")
            self.tick, self.frames = now, 0
            self.ping = (self.ping+1) % 2**31
            self.client.send(dict(t="ping", n=self.ping))
        self.timer = self.after(8, self.poll)
