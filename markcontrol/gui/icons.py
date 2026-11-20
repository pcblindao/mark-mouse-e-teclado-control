import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "assets"


def logo(size=256, active=False):
    """Geometria da marca em assets/logo.svg, sem as duas linhas de texto."""
    ink, accent = ("#651626", "#cf344e") if active else ("#0b3c5d", "#1fb6d5")
    image = Image.new("RGBA", (512, 512))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((12, 12, 500, 500), 96, fill="#ffe3e6" if active else "#e8f5f8")
    d.rounded_rectangle((63, 158, 345, 342), 26, outline=ink, width=20)
    d.line([(204, 342), (204, 384)], fill=ink, width=20)
    d.line([(148, 384), (260, 384)], fill=ink, width=20)
    for box in ((348, 142, 435, 229), (327, 119, 468, 260)):
        d.arc(box, -40, 32, fill=accent, width=12)
    plane = [(221, 205), (394, 127), (341, 282), (296, 232), (261, 253), (268, 210)]
    d.polygon(plane, fill=accent)
    for color, width in (("white", 14), (ink, 8)):
        d.line(plane + plane[:1], fill=color, width=width, joint="curve")
    d.polygon([(268, 210), (361, 150), (296, 232), (261, 253)], fill=ink)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def install_icon(root):
    ico, png = ASSETS / "icon.ico", ASSETS / "icon.png"
    if png.is_file():
        with Image.open(png) as image:
            photo = ImageTk.PhotoImage(image, master=root)
    else:
        photo = ImageTk.PhotoImage(logo(64), master=root)
    root._mark_icon = photo
    root.iconphoto(root is root._root(), photo)
    if sys.platform == "win32" and ico.is_file():
        if root is root._root():
            root.iconbitmap(default=str(ico))
        root.iconbitmap(str(ico))
