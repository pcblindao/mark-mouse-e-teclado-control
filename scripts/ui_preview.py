"""Render the seven Tk reference windows without starting desktop services."""
import argparse
import ctypes
import sys
import threading
import time
import tkinter as tk
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from markcontrol.discovery import Peer
from markcontrol.gui import app, viewer
from markcontrol.gui.theme import enable_dpi, px
from markcontrol.gui.widgets import icon, label

DIGEST = "7ac1f084b29d6a33e8d4c7519a0fbc62d41e57302bc89afa116ee3cd40bf572a"
OFFICE = Peer("192.168.100.104", "Escritório", 47800, ":".join(
    DIGEST[i:i+2] for i in range(0, len(DIGEST), 2)), False, True)
LAPTOP = Peer("192.168.100.108", "Notebook da sala", 47800, ":".join(["aa"]*32), True, False)
CONFIG = dict(name="Meu PC", quality=65, pin=True, clipboard=True)


def capture(window, path):
    window.attributes("-topmost", True)
    window.deiconify()
    window.update_idletasks()
    w, h = window.winfo_width(), window.winfo_height()
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    window.geometry(f"+{max(0, (sw-w)//2)}+{max(0, (sh-h)//2)}")
    window.lift()
    window.update()
    if sys.platform == "win32":
        ctypes.windll.dwmapi.DwmFlush()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    w, h = window.winfo_width(), window.winfo_height()
    shot = None
    if sys.platform == "win32":
        try:
            # Tk's HWND is the client area, inside the native title bar.
            shot = ImageGrab.grab(window=window.winfo_id())
        except TypeError as exc:
            if "window" not in str(exc):  # Pillow before 11.2.1 lacks window=
                raise
    if shot is None:
        if x < 0 or y < 0 or x+w > sw or y+h > sh:
            raise RuntimeError(f"{path.name}: a janela excede a área visível do monitor.")
        shot = ImageGrab.grab(bbox=(x, y, x+w, y+h), all_screens=True)
    if shot.size != (w, h):
        raise RuntimeError(f"{path.name}: a captura {shot.size} difere da janela {(w, h)}.")
    shot.save(path)
    window.withdraw()
    window.update_idletasks()


def render(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    enable_dpi()
    root = tk.Tk()
    root.withdraw()
    with ExitStack() as offline:
        def close():
            for timer in root.tk.splitlist(root.tk.call("after", "info")):
                root.tk.call("after", "cancel", timer)
            root.destroy()
        offline.callback(close)
        config = offline.enter_context(patch.object(app, "Config"))
        config.return_value.refresh.return_value = CONFIG.copy()
        for service in ("AgentRunner", "Tray"):
            offline.enter_context(patch.object(app, service))
        offline.enter_context(patch.object(app.autostart, "enabled", return_value=False))
        offline.enter_context(patch.object(app.App, "poll", lambda self: None))
        offline.enter_context(patch.object(viewer.Viewer, "poll", lambda self: None))
        client = offline.enter_context(patch.object(viewer, "Client"))
        client.return_value.closed = threading.Event()
        ui = app.App(root, None, headless=True)
        ui.active = True
        ui.main_window()
        now = time.perf_counter()
        ui.replies = {p.ip: (now, p) for p in (OFFICE, LAPTOP)}
        ui.update_list()
        ui.show_public_network("wifi")
        capture(root, folder / "01-principal.png")

        ui.active = False
        ui.render_activation()
        ui.show_public_network(None)
        ui.replies.clear()
        ui.empty_since = now-11
        ui.update_list()
        root.minsize(1, 1)
        def size(w, h):
            root.geometry(f"{px(root, w)}x{px(root, h)}")
        size(350, 520)
        ui.header.pack_forget()
        ui.header_line.pack_forget()
        for child in ui.list_heading.pack_slaves()[1:]:
            child.pack_forget()
        capture(root, folder / "02-vazio.png")

        ui.shell.pack_forget()
        size(390, 520)
        ui.settings(host=root)
        capture(root, folder / "03-configuracoes.png")
        ui.close_panel()

        remote = viewer.Viewer(root, OFFICE, CONFIG)
        remote.withdraw()
        remote.stats.set("12 FPS · 18 ms")
        remote.status.set("")
        remote.canvas.configure(bg="#414b56")
        center = tk.Frame(remote.canvas, bg="#414b56")
        center.place(relx=.5, rely=.5, anchor="center")
        icon(center, "monitor", "#cfdae3", size=35).pack()
        label(center, "tela do PC remoto", color="#f0f3f6", face="MarkSection").pack(pady=(px(root, 10), 0))
        for key in ("paste", "copy", "type"):
            button = remote.tools[key][0]
            button.enabled = True
            button.draw()
        capture(remote, folder / "04-tela-remota.png")

        ui.session("192.168.100.111")
        capture(root, folder / "05-sessao-ativa.png")
        ui.session("")
        remote.entry("Entrar com PIN", lambda _: None, True)
        capture(remote.dialog, folder / "06-pin.png")
        remote.identity(OFFICE)
        capture(remote.dialog, folder / "07-identidade.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pasta", type=Path, help="pasta de saída dos sete PNGs")
    render(parser.parse_args().pasta)
