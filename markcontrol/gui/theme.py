import ctypes
import sys
import tkinter.font as tkfont
from tkinter import ttk

TITLE = "Mark Mouse e Teclado Control"
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
    root.option_add("*Font", "TkDefaultFont")
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
