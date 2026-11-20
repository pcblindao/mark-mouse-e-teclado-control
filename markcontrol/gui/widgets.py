from math import cos, pi, sin
import tkinter as tk
from tkinter import font, ttk

from .theme import AMBER, BG, CYAN, GREEN, LINE, MUTED, PANEL, TEXT, px


def label(parent, text="", *, color=TEXT, face="MarkBody", **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=face,
                    borderwidth=0, padx=0, pady=0, anchor="w", **kw)


def rounded(canvas, x, y, w, h, r, fill, outline):
    r = min(r, h / 2, w / 2)
    points = []
    for cx, cy, start in ((x+w-r, y+r, -90), (x+w-r, y+h-r, 0),
                          (x+r, y+h-r, 90), (x+r, y+r, 180)):
        for step in range(9):
            angle = (start + step*90/8) * pi/180
            points.extend((cx + r*cos(angle), cy + r*sin(angle)))
    return canvas.create_polygon(points, fill=fill, outline=outline, tags="surface")


class Card(tk.Canvas):
    def __init__(self, parent, height, fill=PANEL, outline=LINE, *, pad=(13, 8), radius=12, shadow=False, dash=None):
        super().__init__(parent, bg=parent["bg"], bd=0, highlightthickness=0,
                         width=1, height=px(parent, height))
        self.fill, self.outline = fill, outline
        self.pad = tuple(px(self, n) for n in pad)
        self.radius, self.shadow, self.dash = px(self, radius), shadow, dash
        self.body = tk.Frame(self, bg=fill)
        self.window = self.create_window(*self.pad, anchor="nw", window=self.body)
        self.bind("<Configure>", self.layout)

    def layout(self, _=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("surface")
        if min(w, h) < 3:
            return
        if self.shadow:
            for offset, color in ((7, "#0d151e"), (4, "#0b131c")):
                rounded(self, 1, px(self, offset), w-2, h-px(self, 7)-2, self.radius, color, color)
            h -= px(self, 7)
        surface = rounded(self, 1, 1, w-2, h-2, self.radius, self.fill, self.outline)
        if self.dash:
            self.itemconfigure(surface, dash=self.dash)
        self.tag_lower("surface")
        self.itemconfigure(self.window, width=max(1, w-2*self.pad[0]), height=max(1, h-2*self.pad[1]))


# Traçados em uma grade de 24 × 24, compartilhados pelos botões da barra.
GLYPHS = {
    "gear": ((9.8,3,14.2,3,14.8,5.5,16.3,6.4,18.8,5.7,21,9.5,19.2,11.3,19.2,12.7,
              21,14.5,18.8,18.3,16.3,17.6,14.8,18.5,14.2,21,9.8,21,9.2,18.5,7.7,17.6,
              5.2,18.3,3,14.5,4.8,12.7,4.8,11.3,3,9.5,5.2,5.7,7.7,6.4,9.2,5.5,9.8,3),),
    "power": ((12,3,12,12),),
    "monitor": ((5,4,19,4,21.25,5,21.25,15,20,17,4,17,2.75,15,2.75,6,5,4),
                (12,17,12,20), (8,20,16,20)),
    "search": ((11,17,5,17,3,15,3,6,5,4,18,4,20,6,20,10), (11,17,11,20),
               (7,20,12,20), (20,18,22,20)),
    "warning": ((10.4,4.7,12,3.7,13.6,4.7,20.9,17.8,21,19.4,19.3,20.5,4.7,20.5,3,19.4,3.1,17.8,10.4,4.7),
                (12,9,12,13.5), (12,17,12.1,17)),
    "arrow": ((5,12,18,12), (13,7,18,12,13,17)),
    "close": ((6,6,18,18), (18,6,6,18)),
    "down": ((7,10,12,15,17,10),),
    "shield": ((12,3,16,5,20,6,20,11.5,19,15.5,16,18.5,12,21,8,19,5,16,4,11.5,4,6,8,5,12,3),
               (12,8,12,12), (12,15.3,12.1,15.3)),
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
        canvas.create_line(*points, fill=color, width=max(1, size * 1.65 / 24), capstyle="round",
                           joinstyle="round")

    def box(coords):
        return [v * size / 24 + (y if i % 2 else x) for i, v in enumerate(coords)]
    if name in ("gear", "search"):
        bounds = (9.2,9.2,14.8,14.8) if name == "gear" else (14,12,21,19)
        canvas.create_oval(*box(bounds), outline=color, width=max(1, size/14))
    elif name == "power":
        canvas.create_arc(*box((4,4,20,20)), start=124, extent=292, style="arc",
                          outline=color, width=max(1, size/13))


def brand(parent):
    side = px(parent, 40)
    c = tk.Canvas(parent, bg=parent["bg"], width=side, height=side, bd=0, highlightthickness=0)
    rounded(c, 1, 1, side-2, side-2, px(c, 10), "#e8f5f8", "#c4e3ea")
    s = px(c, 34)/438
    def coords(*values):
        return [side/2 + s*(v - (205.5 if i%2 else 264)) for i, v in enumerate(values)]
    x, y = coords(63, 107)
    monitor = rounded(c, x, y, 282*s, 184*s, 26*s, "", "#0b3c5d")
    c.itemconfigure(monitor, width=20*s)
    for line in ((204,291,204,333), (148,333,260,333)):
        c.create_line(*coords(*line), fill="#0b3c5d", width=20*s, capstyle="round")
    for line in ((420,104,435,128,428,154), (445,89,467,129,458,170)):
        c.create_line(*coords(*line), smooth=True, fill=CYAN, width=13*s, capstyle="round")
    plane = coords(221,154,394,76,341,231,296,181,261,202,268,159)
    for color, width in (("white", 14), ("#0b3c5d", 8)):
        c.create_polygon(plane, fill=CYAN, outline=color, width=width*s, joinstyle="round")
    c.create_polygon(coords(268,159,361,99,296,181,261,202), fill="#0b3c5d")
    return c


def icon(parent, name, color, *, size=20, plate=0, fill=None, edge=None):
    side = px(parent, plate or size)
    c = tk.Canvas(parent, width=side, height=side, bg=parent["bg"], bd=0, highlightthickness=0)
    if plate:
        rounded(c, 1, 1, side-2, side-2, px(c, 11), fill or "#17364b", edge or "#2d536a")
    s = px(c, size)
    glyph(c, name, (side-s)/2, (side-s)/2, color, s)
    return c


class Button(tk.Canvas):
    def __init__(self, parent, text, command, width=90, *, fill="#213243", ink=TEXT,
                 tip="", height=34, edge=None, suffix=None):
        super().__init__(parent, bg=parent["bg"], width=px(parent, width), height=px(parent, height),
                         bd=0, highlightthickness=0, takefocus=True)
        self.text, self.command, self.fill, self.ink = text, command, fill, ink
        self.edge = edge or (fill if fill in (CYAN, "#ffda85", "#ffe3e6") else "#3b5267")
        self.suffix = suffix
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

    def configure(self, cnf=None, **kw):
        if "command" in kw:
            self.command = kw.pop("command")
        return super().configure(cnf, **kw)

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
            tk.Label(tip, text=self.tip() if callable(self.tip) else self.tip, bg="#263d50", fg=TEXT, padx=6, pady=3).pack()
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
        edge = CYAN if self.focus_get() == self or self.hover and self.enabled else self.edge if self.enabled else "#314151"
        rounded(self, 1, 1, w-2, h-2, px(self, 10), self.fill if self.enabled else "#1e2b39", edge)
        color = self.ink if self.enabled else "#94a4b4"
        if self.text in GLYPHS:
            size = px(self, 20)
            glyph(self, self.text, (w-size)/2, (h-size)/2, color, size)
        else:
            shift = px(self, 11) if self.suffix else 0
            self.create_text(w/2-shift, h/2, text=self.text, fill=color, font="MarkButton")
            if self.suffix:
                s = px(self, 15)
                glyph(self, self.suffix, w-s-px(self, 9), (h-s)/2, color, s)
        self.configure(cursor="hand2" if self.enabled else "arrow")


class Selector(Button):
    def __init__(self, parent, caption, variable, choices, command, width):
        self.caption, self.variable = caption, variable
        self.choices, self.changed = choices, command
        super().__init__(parent, "", self.choose, width, height=31)
        variable.trace_add("write", lambda *_: self.draw())

    def choose(self):
        menu = tk.Menu(self, tearoff=False, bg="#213243", fg=TEXT, activebackground="#0b3c5d",
                       activeforeground=TEXT)
        for value in self.choices:
            menu.add_radiobutton(label=str(value), value=str(value), variable=self.variable, command=self.changed)
        menu.bind("<Unmap>", lambda _: menu.after_idle(menu.destroy))
        menu.tk_popup(self.winfo_rootx(), self.winfo_rooty()+self.winfo_height())

    def draw(self, _=None):
        super().draw()
        u = lambda n: px(self, n)
        w, y = self.winfo_width(), self.winfo_height()/2
        self.create_text(u(9), y, text=self.caption, anchor="w", fill=MUTED, font="MarkSmall")
        self.create_text(w-u(28), y, text=self.variable.get(), anchor="e", fill=TEXT, font="MarkBadge")
        glyph(self, "down", w-u(23), y-u(7.5), "#98aebf", u(15))


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
        edge = CYAN if self.focus_get() == self or self.hover else ("#4cbf8b" if self.large else "#38a6c1") if on else "#6b7c8c"
        rounded(self, 1, 1, w-2, h-2, h/2, fill, edge)
        pad = px(self, 5 if self.large else 4)
        x = w-h+pad if on else pad
        self.create_oval(x, pad, x+h-2*pad, h-pad, outline="", fill=("#a2f0bd" if self.large else "#8ae5f5") if on else "#d9e3eb")


class Badge(tk.Canvas):
    def __init__(self, parent, text, color):
        f = font.nametofont("MarkBadge", root=parent)
        w, h = f.measure(text)+px(parent, 30), px(parent, 25)
        super().__init__(parent, width=w, height=h, bg=parent["bg"], bd=0, highlightthickness=0)
        fill = {GREEN: "#17382e", AMBER: "#382e22"}.get(color, "#233241")
        rounded(self, 1, 1, w-2, h-2, px(self, 10), fill,
                {GREEN: "#2d604d", AMBER: "#665031"}.get(color, "#395364"))
        r = px(self, 3)
        self.create_oval(3*r, h/2-r, 5*r, h/2+r, fill=color, outline="")
        self.create_text(7*r, h/2, text=text, anchor="w", fill=color, font=f)


def entry_field(parent, *, placeholder="", **options):
    box = Card(parent, 35, "#101b27", "#3a5064", pad=(11, 2), radius=10)
    box.pack(fill="x")
    field = tk.Entry(box.body, **options, bg=box.fill, disabledbackground=box.fill,
                     fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, font="MarkBody")
    field.pack(fill="both", expand=True)
    if placeholder:
        hint = label(box.body, placeholder, color="#a4b6c6", face="MarkBody")
        hint.place(x=0, rely=.5, anchor="w")
        hint.bind("<Button-1>", lambda _: field.focus_set())
        variable = tk.StringVar(master=field, value=field.get())
        field.configure(textvariable=variable)
        field._placeholder_value = variable
        def sync(*_):
            if variable.get():
                hint.place_forget()
            else:
                hint.place(x=0, rely=.5, anchor="w")
        variable.trace_add("write", sync)
    return field


class Slider(tk.Canvas):
    def __init__(self, parent, value):
        super().__init__(parent, bg=BG, bd=0, highlightthickness=0, height=px(parent, 20),
                         width=1, takefocus=True, cursor="hand2")
        self.value = value
        for event in ("<Configure>", "<FocusIn>", "<FocusOut>"):
            self.bind(event, self.draw)
        for event in ("<Button-1>", "<B1-Motion>"):
            self.bind(event, self.drag)
        for key, step in (("Left", -1), ("Right", 1), ("Home", -60), ("End", 60)):
            self.bind(f"<{key}>", lambda _, s=step: self.set(self.value.get()+s))

    def set(self, value):
        self.value.set(max(30, min(90, round(value))))
        self.draw()
        return "break"

    def drag(self, event):
        self.focus_set()
        r = px(self, 8)
        self.set(30 + 60 * (event.x-r)/max(1, self.winfo_width()-2*r))

    def draw(self, _=None):
        self.delete("all")
        r, w, h = px(self, 8), self.winfo_width(), self.winfo_height()/2
        x = r + max(0, w-2*r)*(self.value.get()-30)/60
        for end, color in ((w-2, "#35495c"), (x, CYAN)):
            self.create_line(2, h, end, h, fill=color, width=px(self, 6), capstyle="round")
        self.create_oval(x-r, h-r, x+r, h+r, fill="#a9edf8", width=px(self, 2),
                         outline=CYAN if self.focus_get() == self else "#0e3549")


class Scroll(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0, width=1, height=1)
        style = ttk.Style(self)
        style.layout("Mark.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": True, "sticky": "nswe"})]})])
        style.configure("Mark.Vertical.TScrollbar", troughcolor=BG, background="#395364",
                        bordercolor=BG, lightcolor="#395364", darkcolor="#395364",
                        gripcount=0, arrowsize=px(self, 6), width=px(self, 6))
        style.map("Mark.Vertical.TScrollbar", background=[("active", "#527085")])
        self.bar = ttk.Scrollbar(self, style="Mark.Vertical.TScrollbar", command=self.canvas.yview)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")

        def scroll(first, last):
            self.bar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                self.bar.grid_remove()
            else:
                self.bar.grid()
        self.canvas.configure(yscrollcommand=scroll)
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
        u = lambda n: px(body, n)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        for col, width in ((2, 66), (3, 74), (4, 100)):
            body.grid_columnconfigure(col, minsize=u(width + (11 if col < 4 else 0)))
        icon(body, "monitor", "#8adcf0", plate=42).grid(row=0, column=0, padx=(0, u(11)))
        info = tk.Frame(body, bg=PANEL)
        info.grid(row=0, column=1, sticky="ew", padx=(0, u(11)))
        label(info, name, face="MarkStrong").pack(fill="x")
        label(info, ip, color=MUTED, face="MarkSmall").pack(fill="x", pady=(u(2), 0))
        if self.lookup(iid).pin_required:
            pin = tk.Frame(body, bg=PANEL)
            pin.grid(row=0, column=2, padx=(0, u(11)))
            icon(pin, "lock", MUTED, size=15).pack(side="left")
            label(pin, "PIN", color=MUTED, face="MarkSmall").pack(side="left", padx=(u(4), 0))
        free = state == "Livre"
        Badge(body, state, GREEN if free else AMBER).grid(row=0, column=3, sticky="w")
        action = Button(body, "Controlar", lambda: self.connect(iid), 100, fill=CYAN, ink="#08232e")
        action.enabled = free
        action.grid(row=0, column=4, sticky="e")

    def delete(self, iid):
        self.rows.pop(iid).destroy()
        # A scrolled-out canvas window can defer its Configure event indefinitely.
        self.after_idle(self.resize_region)

    def resize_region(self):
        height = self.body.winfo_reqheight() if self.rows else 0
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
