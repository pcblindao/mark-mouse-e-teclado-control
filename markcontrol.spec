from pathlib import Path
import sys

root = Path(SPECPATH)
backend = "_win32" if sys.platform == "win32" else "_xorg"
data = [(str(root / "markcontrol/static"), "markcontrol/static")]
png = root / "assets/icon.png"
ico = root / "assets/icon.ico"
for asset in (png, ico):
    if asset.is_file():
        data.append((str(asset), "assets"))
icon = next((str(path) for path in (ico, png) if path.is_file()), None)
a = Analysis([str(root / "scripts/start.py")], pathex=[str(root)], datas=data,
             hiddenimports=["pynput.keyboard." + backend, "pynput.mouse." + backend,
                            "pyperclip", "PIL.JpegImagePlugin", "PIL._tkinter_finder"])
pyz = PYZ(a.pure)
name = "Mark Mouse e Teclado Control" if sys.platform == "win32" else "markcontrol"
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=False, icon=icon)
