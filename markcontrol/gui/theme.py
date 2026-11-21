import ctypes
import sys
import tkinter.font as tkfont
from tkinter import ttk
from ..version import __version__

TITLE = "Mark Mouse e Teclado Control"
WINDOW_TITLE = f"{TITLE} v{__version__}"
BG, PANEL, FIELD, LINE = "#131e2a", "#192736", "#101b27", "#2c4052"
TEXT, MUTED, CYAN = "#f3f7fb", "#aabccc", "#1fb6d5"
GREEN, AMBER, RED = "#73e0a5", "#ffdb86", "#9a293a"


def enable_dpi():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def px(widget, size):
    return round(size * widget.winfo_fpixels("1i") / 96)


def install(root):
    dpi = root.winfo_fpixels("1i")
    if sys.platform == "win32":
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id()) or dpi
        except AttributeError:
            pass
    root.tk.call("tk", "scaling", dpi / 72)
    family = "Segoe UI" if "Segoe UI" in tkfont.families(root) else "DejaVu Sans"
    for name, size, weight in (("TkDefaultFont", 10, "normal"), ("TkTextFont", 10, "normal"),
                               ("TkHeadingFont", 13, "bold"), ("TkSmallCaptionFont", 9, "normal")):
        tkfont.nametofont(name, root=root).configure(family=family, size=size, weight=weight)
    # CSS uses pixels. Negative Tk font sizes are device pixels.
    families = tkfont.families(root)
    for name, size, bold in (
        ("Body", 14, False), ("Strong", 14, True), ("Title", 18, True),
        ("Activation", 21, True), ("Section", 16, True), ("Sheet", 19, True),
        ("Dialog", 17, True), ("Empty", 15, True), ("Small", 12, False),
        ("Badge", 12, True), ("Button", 13, True), ("Detail", 13, False),
        ("Digest", 11, False),
    ):
        weight = "bold" if bold else "normal"
        face = "Segoe UI Semibold" if bold and size <= 14 and "Segoe UI Semibold" in families else family
        root.tk.call("font", "create", "Mark" + name, "-family", face,
                     "-size", -px(root, size), "-weight", "normal" if face != family else weight)
    root.option_add("*Font", "MarkBody")
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD,
                    bordercolor=LINE, arrowcolor=TEXT, font="TkDefaultFont")
    style.map(".", background=[("active", PANEL), ("selected", "#0b3c5d")],
              foreground=[("disabled", MUTED), ("selected", TEXT)])
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
              foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
