import tkinter as tk
from tkinter import font, ttk

from .theme import AMBER, BG, CYAN, GREEN, LINE, MUTED, PANEL, TEXT, px


def label(parent, text="", *, color=TEXT, face="TkDefaultFont", **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=face,
                    borderwidth=0, anchor="w", **kw)


def rounded(canvas, x, y, w, h, r, fill, outline):
    r = min(r, h / 2, w / 2)
    return canvas.create_polygon(x+r,y, x+w-r,y, x+w,y, x+w,y+r, x+w,y+h-r,
                                 x+w,y+h, x+w-r,y+h, x+r,y+h, x,y+h, x,y+h-r,
                                 x,y+r, x,y, smooth=True, splinesteps=20,
                                 fill=fill, outline=outline, tags="surface")


class Card(tk.Canvas):
    def __init__(self, parent, height, fill=PANEL, outline=LINE):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0,
                         width=1, height=px(parent, height))
        self.fill, self.outline = fill, outline
        self.body = tk.Frame(self, bg=fill)
        self.window = self.create_window(px(self, 14), px(self, 8), anchor="nw", window=self.body)
        self.bind("<Configure>", self.layout)

    def layout(self, _=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("surface")
        rounded(self, 1, 1, w-2, h-2, px(self, 12), self.fill, self.outline)
        self.tag_lower("surface")
        self.itemconfigure(self.window, width=max(1, w-px(self, 28)), height=max(1, h-px(self, 16)))


# Traçados em uma grade de 24 × 24, compartilhados pelos botões da barra.
GLYPHS = {
    "paste": ((8,5,5,5,5,21,19,21,19,5,16,5), (9,3,15,3,15,7,9,7,9,3),
              (12,10,12,17), (9,14,12,17,15,14)),
    "copy": ((8,8,20,8,20,20,8,20,8,8), (16,6,16,4,4,4,4,16,6,16)),
    "type": ((2,6,22,6,22,18,2,18,2,6), (6,10,8,10), (11,10,13,10),
             (16,10,18,10), (8,14,16,14)),
    "full": ((4,9,4,4,9,4), (15,4,20,4,20,9), (20,15,20,20,15,20), (9,20,4,20,4,15)),
    "exit": ((11,5,4,5,4,19,11,19), (10,12,21,12), (16,7,21,12,16,17)),
    "file": ((5,3,14,3,19,8,19,21,5,21,5,3), (14,3,14,8,19,8), (12,18,12,11),
             (9,14,12,11,15,14)),
    "eye": ((2,12,6,7,12,5,18,7,22,12,18,17,12,19,6,17,2,12),
            (9,12,12,9,15,12,12,15,9,12)),
    "keys": ((3,5,9,5,9,11,3,11,3,5), (14,5,20,5,20,11,14,11,14,5),
             (6,15,17,15,17,20,6,20,6,15)),
    "chat": ((3,4,21,4,21,16,10,16,5,21,5,16,3,16,3,4), (7,9,17,9)),
    "lock": ((5,10,19,10,19,21,5,21,5,10), (8,10,8,6,10,3,14,3,16,6,16,10),
             (12,14,12,17)),
}


def glyph(canvas, name, x, y, color, size):
    for stroke in GLYPHS[name]:
        points = [(x if i % 2 == 0 else y) + v * size / 24 for i, v in enumerate(stroke)]
        canvas.create_line(*points, fill=color, width=max(1, size / 12), capstyle="round",
                           joinstyle="round")


class Button(tk.Canvas):
    def __init__(self, parent, text, command, width=90, *, fill="#213243", ink=TEXT,
                 tip="", height=34):
        super().__init__(parent, bg=parent["bg"], width=px(parent, width), height=px(parent, height),
                         bd=0, highlightthickness=0, takefocus=True)
        self.text, self.command, self.fill, self.ink = text, command, fill, ink
        self.tip, self.tip_window = tip, None
        self.hover, self.enabled = False, True
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _: self.highlight(True))
        self.bind("<Leave>", lambda _: self.highlight(False))
        self.bind("<FocusIn>", self.draw)
        self.bind("<FocusOut>", self.draw)
        self.bind("<Button-1>", self.invoke)
        for key in ("<Return>", "<space>"):
            self.bind(key, self.invoke)

    def highlight(self, inside):
        self.hover = inside
        self.draw()
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None
        if inside and self.tip:
            tip = self.tip_window = tk.Toplevel(self)
            tip.withdraw()
            tip.overrideredirect(True)
            tk.Label(tip, text=self.tip, bg="#263d50", fg=TEXT, padx=6, pady=3).pack()
            tip.update_idletasks()
            x = min(self.winfo_rootx(), self.winfo_screenwidth()-tip.winfo_reqwidth())
            tip.geometry(f"+{max(0, x)}+{self.winfo_rooty()+self.winfo_height()+2}")
            tip.deiconify()

    def invoke(self, _=None):
        if self.enabled:
            self.highlight(False)
            self.command()
        return "break"

    def draw(self, _=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if min(w, h) < 3:
            return
        edge = CYAN if self.focus_get() == self or self.hover and self.enabled else LINE
        rounded(self, 1, 1, w-2, h-2, px(self, 10), self.fill if self.enabled else "#1e2b39", edge)
        color = self.ink if self.enabled else MUTED
        if self.text in GLYPHS:
            size = px(self, 20)
            glyph(self, self.text, (w-size)/2, (h-size)/2, color, size)
        else:
            self.create_text(w/2, h/2, text=self.text, fill=color, font="TkDefaultFont")
        self.configure(cursor="hand2" if self.enabled else "arrow")


class Switch(Button):
    def __init__(self, parent, variable, command=None, large=False):
        self.variable, self.large = variable, large
        def toggle():
            if command:
                command()
            else:
                variable.set(not variable.get())
            self.draw()
        super().__init__(parent, "", toggle, 58 if large else 42, height=32 if large else 24)

    def draw(self, _=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if min(w, h) < 3:
            return
        on = self.variable.get()
        fill = ("#215f49" if self.large else "#0b3c5d") if on else "#344353"
        edge = CYAN if self.focus_get() == self or self.hover else "#6b7c8c"
        rounded(self, 1, 1, w-2, h-2, h/2, fill, edge)
        pad = px(self, 4)
        x = w-h+pad if on else pad
        self.create_oval(x, pad, x+h-2*pad, h-pad, outline="", fill=GREEN if on else "#d9e3eb")


class Badge(tk.Canvas):
    def __init__(self, parent, text, color):
        f = font.nametofont("TkSmallCaptionFont")
        w, h = f.measure(text)+px(parent, 28), px(parent, 24)
        super().__init__(parent, width=w, height=h, bg=parent["bg"], bd=0, highlightthickness=0)
        fill = {GREEN: "#17382e", AMBER: "#382e22"}.get(color, "#233241")
        rounded(self, 1, 1, w-2, h-2, px(self, 10), fill, LINE)
        r = px(self, 3)
        self.create_oval(2*r, h/2-r, 4*r, h/2+r, fill=color, outline="")
        self.create_text(6*r, h/2, text=text, anchor="w", fill=color, font=f)


class Scroll(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0, width=1, height=1)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        bar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

    def wheel(self, event):
        w = event.widget
        while w is not None and w != self:
            w = getattr(w, "master", None)
        if w == self:
            up = event.num == 4 or event.delta > 0
            self.canvas.yview_scroll(-2 if up else 2, "units")
            return "break"


class PeerList(Scroll):
    def __init__(self, parent, lookup, connect):
        super().__init__(parent)
        self.rows = {}
        self.lookup, self.connect = lookup, connect

    def insert(self, parent, where, *, iid, values):
        row = self.rows[iid] = Card(self.body, 70)
        row.pack(fill="x", pady=(0, px(self, 8)))
        self.item(iid, values)

    def item(self, iid, values):
        body = self.rows[iid].body
        for child in body.winfo_children():
            child.destroy()
        name, ip, state = values
        action = Button(body, "Controlar", lambda: self.connect(iid), 94, fill=CYAN, ink="#08232e")
        action.enabled = state == "Livre"
        action.pack(side="right", padx=4)
        Badge(body, state, GREEN if action.enabled else AMBER).pack(side="right", padx=8)
        if self.lookup(iid).pin_required:
            label(body, "PIN", color=MUTED).pack(side="right", padx=(0, 4))
            lock = tk.Canvas(body, bg=PANEL, width=px(self, 18), height=px(self, 18),
                             bd=0, highlightthickness=0)
            lock.pack(side="right")
            glyph(lock, "lock", 1, 1, MUTED, px(self, 15))
        monitor = tk.Canvas(body, bg="#17364b", width=px(self, 42), height=px(self, 42),
                            bd=0, highlightthickness=0)
        monitor.pack(side="left", padx=(0, px(self, 11)))
        u = lambda n: px(self, n)
        monitor.create_rectangle(u(9), u(11), u(33), u(27), outline="#8adcf0", width=u(2))
        monitor.create_line(u(21), u(27), u(21), u(32), fill="#8adcf0", width=u(2))
        monitor.create_line(u(15), u(32), u(27), u(32), fill="#8adcf0", width=u(2))
        info = tk.Frame(body, bg=PANEL)
        info.pack(side="left", fill="x", expand=True)
        label(info, name).pack(fill="x")
        label(info, ip, color=MUTED, face="TkSmallCaptionFont").pack(fill="x")

    def delete(self, iid):
        self.rows.pop(iid).destroy()
