import os
import sys
import threading
import time
import tkinter as tk
from queue import Empty, SimpleQueue
from tkinter import messagebox, ttk

from PIL import ImageTk

from ..agent_runner import AgentRunner
from ..config import Config
from ..discovery import MAX_PEERS, grouped_peers, scan_cycle
from . import autostart, network
from .icons import install_icon, logo
from .theme import AMBER, BG, CYAN, FIELD, GREEN, LINE, MUTED, RED, TEXT, TITLE, enable_dpi, install, px
from .tray import Tray
from .viewer import Viewer
from .widgets import Badge, Button, Card, PeerList, Scroll, Switch, label


class App:
    def __init__(self, root, args, headless):
        self.root, self.args, self.headless = root, args, headless
        self.config = Config()
        self.runner = AgentRunner()
        self.peers = {}
        self.replies = {}
        self.primary = None
        self.empty_since = time.perf_counter()
        self.found = SimpleQueue()
        self.done = threading.Event()
        self.session_ip = ""
        root.bind("<Unmap>", self.keep_visible)
        self.active = False
        self.fingerprint = ""
        self.error = ""
        self.warning = tk.StringVar()
        cfg = self.config.refresh(self.warning.set, opening=True)
        if cfg.get("review_required"):
            self.warning.set("config.json.bad preservado. Revise o PIN e salve em Configurar.")
            self.headless = False
        self.name = tk.StringVar(value=cfg["name"])
        self.status = tk.StringVar()
        self.hint = tk.StringVar()
        root.title("Mark Mouse e Teclado Control")
        install(root)
        install_icon(root)
        self.pending = self.notified = False
        self.tray = Tray(self.runner.events)
        self.update_offer = None
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self.banner = tk.Frame(root, bg=RED, padx=12, pady=10)
        Button(self.banner, "Encerrar agora", self.runner.stop, 116,
               fill="#ffe3e6", ink="#651626").pack(side="right")
        self.banner_text = label(self.banner, wraplength=px(root, 365), justify="left")
        self.banner_text.pack(side="left", fill="x", expand=True)
        if self.headless:
            root.withdraw()
        else:
            self.main_window()
            threading.Thread(target=self.discover, daemon=True).start()
        root.after(50, self.poll)

    def main_window(self):
        root = self.root
        u = lambda n: px(root, n)
        root.geometry(f"{u(760)}x{u(520)}")
        root.minsize(u(530), u(400))
        self.main = tk.Frame(root, bg=BG)
        self.main.pack(fill="both", expand=True)
        top = tk.Frame(self.main, bg=BG, padx=u(23), pady=u(12))
        top.pack(fill="x")
        self.brand = ImageTk.PhotoImage(logo(u(40)), master=root)
        label(top, image=self.brand).pack(side="left", padx=(0, u(12)))
        label(top, TITLE, face="TkHeadingFont").pack(side="left")
        Button(top, "⚙", self.settings, 36, tip="Configurações").pack(side="right")
        self.update_slot = tk.Frame(top, bg=BG)
        self.update_slot.pack(side="right", padx=6)
        self.render_update()
        tk.Frame(self.main, bg=LINE, height=1).pack(fill="x")
        body = tk.Frame(self.main, bg=BG, padx=u(23), pady=u(15))
        body.pack(fill="both", expand=True)
        self.activation = Card(body, 92)
        self.activation.pack(fill="x", pady=(0, u(12)))
        self.enabled = tk.BooleanVar(value=self.active)
        self.render_activation()
        self.network_panel = tk.Frame(body, bg="#34291c", padx=9, pady=5)
        self.network_button = ttk.Button(self.network_panel, text="Abrir configurações de rede")
        self.network_button.pack(side="right")
        label(self.network_panel, "⚠  Rede Pública", color=AMBER).pack(fill="x")
        self.network_text = label(self.network_panel, color="#e2ccaa", face="TkSmallCaptionFont",
                                  wraplength=u(330), justify="left")
        self.network_text.pack(fill="x")
        self.list_heading = tk.Frame(body, bg=BG)
        self.list_heading.pack(fill="x", pady=(0, u(7)))
        label(self.list_heading, "PCs encontrados", face="TkHeadingFont").pack(side="left")
        label(self.list_heading, "Procurando PCs…", color=MUTED).pack(side="right")
        self.pulse = label(self.list_heading, color=CYAN)
        self.pulse.pack(side="right", padx=4)
        self.animate()
        for var, color in ((self.warning, AMBER), (self.status, MUTED)):
            line = label(body, textvariable=var, color=color, wraplength=u(485), justify="left")
            line.pack(side="bottom", fill="x")
        self.tree = PeerList(body, lambda fp: self.peers[fp][1], self.connect)
        self.tree.pack(fill="both", expand=True)
        self.empty = Card(self.tree.body, 148, fill="#14212e")
        label(self.empty.body, "⌕", color=CYAN, face="TkHeadingFont").pack(pady=(u(8), 2))
        label(self.empty.body, "Nenhum PC encontrado.", face="TkHeadingFont").pack()
        label(self.empty.body, "No outro PC, abra o app e clique Ativar.", color=MUTED).pack()
        self.panel = None
        self.hint.trace_add("write", lambda *_: self.empty_state())
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind(event, self.wheel, add=True)

    def wheel(self, event):
        if self.panel and self.settings_scroll.wheel(event):
            return "break"
        return self.tree.wheel(event)

    def render_activation(self):
        card = self.activation
        for child in card.body.winfo_children():
            child.destroy()
        card.fill = "#172e2c" if self.active else "#1a2734"
        card.outline = "#315b4c" if self.active else LINE
        card.body.configure(bg=card.fill)
        card.layout()
        self.enabled.set(self.active)
        self.toggle = Switch(card.body, self.enabled, self.toggle_agent, large=True)
        self.toggle.enabled = not self.pending
        self.toggle.pack(side="right")
        label(card.body, "⏻", color=GREEN if self.active else MUTED,
              face="TkHeadingFont").pack(side="left", padx=10)
        words = tk.Frame(card.body, bg=card.fill)
        words.pack(side="left")
        title = tk.Frame(words, bg=card.fill)
        title.pack(anchor="w")
        label(title, "Ativar este PC", face="TkHeadingFont").pack(side="left")
        Badge(title, "Ativo" if self.active else "Desativado",
              GREEN if self.active else MUTED).pack(side="left", padx=8)
        label(words, "Outros PCs podem controlar este" if self.active else
              "Este PC não aceita controle remoto.", color=MUTED).pack(anchor="w")

    def animate(self, phase=0):
        if not self.done.is_set():
            self.pulse.configure(text=("● · ·", "· ● ·", "· · ●")[phase])
            self.root.after(350, self.animate, (phase+1) % 3)

    def empty_state(self):
        if self.hint.get():
            if not self.empty.winfo_manager():
                self.empty.pack(fill="x")
        elif self.empty.winfo_manager():
            self.empty.pack_forget()

    def show_update(self, version, on_click):
        self.runner.events.put(("update-offer", (version, on_click)))

    def render_update(self):
        for child in self.update_slot.winfo_children():
            child.destroy()
        if self.update_offer and not self.session_ip:
            version, callback = self.update_offer
            Button(self.update_slot, f"Atualizar {version}", callback, 115).pack()

    def close_panel(self):
        if self.panel:
            self.panel.destroy()
            self.panel = None

    def settings(self):
        if self.panel:
            self.close_panel()
            return
        cfg = self.config.refresh(self.warning.set)
        panel = self.panel = tk.Frame(self.main, bg=BG, highlightthickness=1, highlightbackground=LINE)
        panel.place(relx=1, y=0, relheight=1, width=px(panel, 390), anchor="ne")
        bar = tk.Frame(panel, bg=BG, padx=12, pady=10)
        bar.pack(fill="x")
        label(bar, "⚙  Configurações", face="TkHeadingFont").pack(side="left")
        Button(bar, "×", self.close_panel, 34, tip="Fechar configurações").pack(side="right")
        footer = tk.Frame(panel, bg=BG, padx=10, pady=8)
        footer.pack(side="bottom", fill="x")
        scroll = self.settings_scroll = Scroll(panel)
        scroll.pack(fill="both", expand=True, padx=12)
        form = scroll.body
        name = tk.StringVar(value=cfg["name"])
        quality = tk.IntVar(value=cfg["quality"])
        clip = tk.BooleanVar(value=cfg["clipboard"])
        protected = tk.BooleanVar(value=cfg["pin"] is not None)
        auto = tk.BooleanVar(value=autostart.enabled())

        def option(title, var, command=None):
            row = tk.Frame(form, bg=BG)
            row.pack(fill="x", pady=4)
            label(row, title).pack(side="left")
            Switch(row, var, command).pack(side="right")

        label(form, "Nome do PC").pack(anchor="w")
        name_field = ttk.Entry(form, textvariable=name)
        name_field.pack(fill="x", pady=4)
        field = ttk.Entry(form, show="•")
        def protect():
            protected.set(not protected.get())
            field.configure(state="normal" if protected.get() else "disabled")
        option("Exigir PIN", protected, protect)
        field.pack(fill="x")
        field.configure(state="normal" if protected.get() else "disabled")
        label(form, "8 dígitos. Deixe em branco para manter o PIN atual.", color=MUTED,
              face="TkSmallCaptionFont").pack(anchor="w", pady=3)
        option("Permitir copiar e colar", clip)
        qrow = tk.Frame(form, bg=BG)
        qrow.pack(fill="x")
        label(qrow, "Qualidade").pack(side="left")
        label(qrow, textvariable=quality, color=CYAN).pack(side="right")
        tk.Scale(form, from_=30, to=90, resolution=1, orient="horizontal", variable=quality,
                 showvalue=False, bg=BG, fg=TEXT, troughcolor=FIELD, activebackground=CYAN,
                 highlightthickness=0, bd=0).pack(fill="x")
        limits = tk.Frame(form, bg=BG)
        limits.pack(fill="x")
        for value, side in (("30", "left"), ("90", "right")):
            label(limits, value, color=MUTED, face="TkSmallCaptionFont").pack(side=side)
        option("Ativar ao iniciar o " + ("Windows" if sys.platform == "win32" else "sistema"), auto)
        feedback = label(form, color=AMBER, wraplength=px(form, 325), justify="left")

        def save():
            nonlocal cfg
            try:
                pin = field.get() if protected.get() else ""
                if protected.get() and not pin:
                    if not cfg["pin"]:
                        raise ValueError("Defina um PIN de oito dígitos.")
                    pin = None
                self.config.save(name.get(), clip.get(), quality.get(), pin)
                cfg = self.config.refresh(self.warning.set)
                self.warning.set("")
                self.name.set(name.get())
                field.delete(0, "end")
                if auto.get() != autostart.enabled():
                    try:
                        autostart.set_enabled(auto.get())
                    except Exception as exc:
                        feedback.configure(text=f"Preferências salvas. Falha na inicialização: {exc}")
                        return
                self.close_panel()
                self.status.set("Configuração salva.")
            except (ValueError, OSError) as exc:
                feedback.configure(text=str(exc))

        def forget():
            if messagebox.askyesno("Esquecer aparelhos", "Revogar todos os pareamentos deste PC?",
                                   parent=self.root):
                try:
                    self.config.forget()
                    feedback.configure(text="Pareamentos revogados.")
                except (ValueError, OSError) as exc:
                    feedback.configure(text=str(exc))

        Button(form, "Esquecer aparelhos", forget, 150).pack(anchor="w", pady=4)
        if self.fingerprint:
            label(form, "SHA-256  " + self.fingerprint, color=MUTED, wraplength=px(form, 330),
                  justify="left").pack(anchor="w")
        feedback.pack(fill="x")
        Button(footer, "Sair", self.close, 48).pack(side="left")
        Button(footer, "Salvar", save, 76, fill=CYAN, ink=FIELD).pack(side="right")
        Button(footer, "Cancelar", self.close_panel, 82).pack(side="right", padx=5)
        name_field.focus_set()

    def toggle_agent(self):
        if self.pending or self.done.is_set():
            return
        self.pending = True
        if self.active:
            self.runner.stop()
        else:
            self.runner.start(self.args)
        if not self.headless:
            self.toggle.enabled = False

    def discover(self):
        while not self.done.is_set():
            started = time.monotonic()
            try:
                self.found.put(scan_cycle(1.2, "255.255.255.255"))
            except OSError as exc:
                self.found.put(str(exc))
            self.done.wait(max(0, 3 - (time.monotonic()-started)))

    def connect(self, fingerprint=None):
        selection = (fingerprint,) if fingerprint is not None else self.tree.selection()
        if selection and selection[0] in self.peers:
            _, peer = self.peers[selection[0]]
            if peer.busy:
                self.status.set("Este PC está em uso.")
            else:
                Viewer(self.root, peer, self.config.refresh(self.warning.set))

    def update_list(self):
        while not self.found.empty():
            result = self.found.get()
            if isinstance(result, str):
                self.status.set(result)
                continue
            self.primary, replies = result
            self.replies.update(replies)

        now = time.perf_counter()
        self.replies = {ip: (stamp, peer) for ip, (stamp, peer) in self.replies.items()
                        if now - stamp < 10 and peer.fingerprint != self.fingerprint}
        if len(self.replies) > MAX_PEERS:
            self.replies = dict(sorted(self.replies.items(), key=lambda item: item[1][0],
                                       reverse=True)[:MAX_PEERS])
        peers = grouped_peers(self.replies, self.primary)
        previous, self.peers = self.peers, peers
        for fingerprint in previous.keys() - peers.keys():
            self.tree.delete(fingerprint)
        for fingerprint, (shown, latest) in peers.items():
            if previous.get(fingerprint) == (shown, latest):
                continue
            row = (latest.name, shown.ip, "Em uso" if latest.busy else "Livre")
            if fingerprint in previous:
                self.tree.item(fingerprint, values=row)
            else:
                self.tree.insert("", "end", iid=fingerprint, values=row)

        if peers:
            self.empty_since = None
        elif self.empty_since is None:
            self.empty_since = now
        self.hint.set(
            "Nenhum PC encontrado. No outro PC, abra o Mark Mouse e Teclado Control e clique Ativar."
            if self.empty_since is not None and now - self.empty_since > 10 else ""
        )

    def show_public_network(self, kind):
        if kind is None:
            self.network_panel.pack_forget()
            return
        label = "Wi-Fi" if kind == "wifi" else "Ethernet"
        subject = "O Wi-Fi está" if kind == "wifi" else "A Ethernet está"
        self.network_text.configure(text=(
            f"{subject} como rede Pública: o Windows bloqueia o Mark Mouse e Teclado Control. "
            f"Abra Configurações > Rede > {label} > esta rede > Perfil: Privada."
        ))
        self.network_button.configure(command=lambda: self.open_network_settings(kind))
        self.network_panel.pack(before=getattr(self, "list_heading", self.tree), fill="x", pady=(0, 6))

    def open_network_settings(self, kind):
        try:
            os.startfile(network.SETTINGS[kind])
        except OSError:
            self.status.set("Abra as Configurações de rede do Windows para mudar o perfil.")

    def check_public_network(self):
        activation = self.runner.thread

        def check():
            self.runner.events.put(("public-network", (activation, network.public_network())))

        threading.Thread(target=check, daemon=True).start()

    def keep_visible(self, event):
        if event.widget is self.root and not self.done.is_set():
            self.root.after_idle(self.on_unmap)

    def on_unmap(self):
        if self.session_ip:
            self.root.deiconify()
        elif self.root.state() == "iconic" and self.tray.ready.is_set():
            self.hide()

    def hide(self):
        if self.session_ip:
            self.root.deiconify()
            self.root.lift()
        elif self.tray.ready.is_set():
            if not self.notified:
                self.notified = True
                if self.tray.icon.HAS_NOTIFICATION:
                    try:
                        self.tray.icon.notify("continua rodando na bandeja", TITLE)
                    except (OSError, NotImplementedError):
                        messagebox.showinfo(TITLE, "continua rodando na bandeja", parent=self.root)
                else:
                    messagebox.showinfo(TITLE, "continua rodando na bandeja", parent=self.root)
            if not self.session_ip and self.tray.ready.is_set():
                self.root.withdraw()
        else:
            self.root.iconify()

    def tray_action(self, action):
        if action == "ready":
            self.tray.update(self.active, self.session_ip)
        elif action == "open":
            if self.headless:
                self.headless = False
                self.main_window()
                if self.active and sys.platform == "win32":
                    self.check_public_network()
                threading.Thread(target=self.discover, daemon=True).start()
            self.root.deiconify()
            self.root.lift()
        elif action == "unavailable":
            if not self.headless and self.root.state() == "withdrawn":
                self.root.deiconify()
                if not self.session_ip:
                    self.root.iconify()
        elif action == "toggle":
            self.toggle_agent()
        elif action == "exit":
            self.close()
        elif action == "menu":
            menu = tk.Menu(self.root, tearoff=False)
            menu.add_command(label="Abrir", command=lambda: self.tray_action("open"))
            menu.add_command(label=self.tray.toggle_label, command=self.toggle_agent)
            menu.add_command(label="Sair", command=self.close)
            menu.bind("<Unmap>", lambda _: menu.after_idle(menu.destroy))
            menu.tk_popup(*self.root.winfo_pointerxy())

    def session(self, ip):
        self.session_ip = ip
        self.tray.update(self.active, ip)
        if not self.headless:
            self.render_update()
        if ip:
            title = f"Mark Mouse e Teclado Control · SESSÃO ATIVA de {ip} — Ctrl+Alt+Shift+Q encerra"
            self.root.title(title)
            self.banner_text.configure(text=f"Este PC está sendo controlado por {ip} — Ctrl+Alt+Shift+Q encerra")
            if self.headless:
                self.banner.pack(fill="x")
            else:
                self.banner.pack(before=self.main, fill="x")
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.bell()
        else:
            self.root.attributes("-topmost", False)
            self.root.title("Mark Mouse e Teclado Control")
            self.banner.pack_forget()
            if self.headless:
                self.root.withdraw()

    def poll(self):
        try:
            while True:
                kind, value = self.runner.events.get_nowait()
                if kind == "tray":
                    if not self.done.is_set():
                        self.tray_action(value)
                elif kind == "update-offer":
                    self.update_offer = value
                    if not self.headless:
                        self.render_update()
                elif kind == "session":
                    self.session(value)
                elif kind == "warning":
                    self.warning.set(value)
                    if self.headless:
                        if self.session_ip:
                            self.banner_text.configure(text=f"{self.root.title()}\n{value}")
                        print(value, file=sys.stderr)
                elif kind == "error":
                    self.error = value
                    self.status.set(value)
                    if self.headless:
                        print(value, file=sys.stderr)
                elif kind == "public-network":
                    activation, profile = value
                    if self.active and activation is self.runner.thread:
                        self.show_public_network(profile)
                elif kind in ("ready", "off"):
                    self.pending = False
                    self.active = kind == "ready"
                    self.tray.update(self.active, self.session_ip)
                    self.fingerprint = value
                    if not self.active:
                        self.session("")
                        if self.headless:
                            self.close()
                    if not self.headless:
                        self.render_activation()
                        if self.active and sys.platform == "win32":
                            self.check_public_network()
                        elif not self.active:
                            self.show_public_network(None)
        except Empty:
            pass
        if self.done.is_set():
            if not self.runner.thread or not self.runner.thread.is_alive():
                self.root.destroy()
                return
        elif not self.headless:
            self.update_list()
        self.root.after(80, self.poll)

    def close(self):
        self.done.set()
        self.tray.stop()
        self.runner.stop()


def launch(args, headless, auto):
    enable_dpi()
    root = tk.Tk()
    app = App(root, args, headless)
    if not app.config.last_good.get("review_required") and (headless or auto and autostart.enabled()):
        app.toggle_agent()
    try:
        root.mainloop()
    finally:
        app.close()
        if app.runner.thread:
            app.runner.thread.join()
    return 2 if headless and app.error else 0
