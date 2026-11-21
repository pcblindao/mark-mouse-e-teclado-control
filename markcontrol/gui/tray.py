import sys
import threading
from contextlib import closing

from .icons import logo
from .theme import TITLE, WINDOW_TITLE
from ..diagnostics import event


class Tray:
    def __init__(self, events):
        self.events = events
        self.icon = None
        self.active = False
        self.desired_active = None
        self.session = False
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.images = (logo(64), logo(64, active=True))
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            from pystray import Menu, MenuItem
            if sys.platform == "win32":
                from pystray._win32 import Icon
            else:
                from pystray._xorg import Icon
            default = "open" if Icon.HAS_MENU else "menu"
            menu = Menu(MenuItem("Abrir", lambda: self.post(default), default=True),
                        MenuItem(lambda _: self.toggle_label, lambda: self.post("toggle")),
                        MenuItem("Sair", lambda: self.post("exit")))
            self.icon = Icon("markcontrol", self.images[self.session], WINDOW_TITLE, menu)
            self.icon.run(self.setup)
        except Exception as exc:
            # Backend opcional, inclusive em desktops sem área de notificação.
            event("tray_failed", exc)
        finally:
            self.ready.clear()
            self.post("unavailable")

    @property
    def toggle_label(self):
        desired = self.active if self.desired_active is None else self.desired_active
        return "Desativar este PC" if desired else "Ativar este PC"

    def post(self, action):
        self.events.put(("tray", action))

    def setup(self, icon):
        try:
            if sys.platform == "win32":
                icon.visible = True
                self.ready.set()
                event("tray_ready")
                self.post("ready")
                self.finished.wait()
            else:
                from Xlib.display import Display
                with closing(Display()) as display:
                    atom = display.intern_atom(f"_NET_SYSTEM_TRAY_S{display.get_default_screen()}")
                    while not self.finished.is_set():
                        available = bool(display.get_selection_owner(atom))
                        if available != self.ready.is_set():
                            icon.visible = available
                            if available:
                                self.ready.set()
                                self.post("ready")
                            else:
                                self.ready.clear()
                                self.post("unavailable")
                        self.finished.wait(0.75)
        finally:
            self.ready.clear()
            icon.stop()

    def update(self, active, session):
        self.active, self.session = active, bool(session)
        if self.ready.is_set():
            try:
                self.icon.icon = self.images[self.session]
                self.icon.title = WINDOW_TITLE + (" — Sessão de controle ativa" if self.session else "")
                self.icon.update_menu()
            except Exception as exc:
                event("tray_update_failed", exc)
                self.ready.clear()
                self.finished.set()
                self.post("unavailable")

    def stop(self):
        self.finished.set()
