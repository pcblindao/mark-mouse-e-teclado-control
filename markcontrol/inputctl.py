import asyncio
import sys

from .security import NAMED_KEYS
from .diagnostics import event

COMBO_STEP = 0.03
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


def unicode_units(char):
    """Unidades UTF-16 de um caractere (par substituto acima de 0xFFFF)."""
    data = char.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def send_unicode(char):
    """Digita via KEYEVENTF_UNICODE: independe do Caps Lock e do layout do PC remoto."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", UNION)]

    events = [INPUT(type=1, u=UNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | up, 0, 0)))
              for unit in unicode_units(char) for up in (0, KEYEVENTF_KEYUP)]
    array = (INPUT * len(events))(*events)
    if ctypes.windll.user32.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        raise OSError("O sistema recusou a digitação.")


class Input:
    def __init__(self):
        from pynput import keyboard, mouse

        self.keyboard_module = keyboard
        self.keyboard = keyboard.Controller()
        self.mouse = mouse.Controller()
        self.keys = {name: getattr(keyboard.Key, key) for name, key in NAMED_KEYS.items()}
        self.buttons = {name: getattr(mouse.Button, name) for name in ("left", "right", "middle")}
        self.held_keys = set()
        self.held_buttons = set()

    def hotkey(self, callback):
        return self.keyboard_module.GlobalHotKeys({"<ctrl>+<alt>+<shift>+q": callback})

    @staticmethod
    def point(message, region):
        return (
            region["left"] + round(message["x"] * (region["width"] - 1)),
            region["top"] + round(message["y"] * (region["height"] - 1)),
        )

    def _move(self, message, region):
        self.mouse.position = self.point(message, region)

    @staticmethod
    def _press(controller, held, value, direction):
        if direction == "down":
            # Repetição automática: tecla já pressionada recebe outro press, como no
            # teclado local; um único "up" a solta.
            controller.press(value)
            held.add(value)
        elif direction == "up" and value in held:
            controller.release(value)
            held.discard(value)

    async def apply(self, message, region, active):
        kind = message["t"]
        if kind in ("move", "click"):
            self._move(message, region)
        if kind == "click":
            self._press(self.mouse, self.held_buttons, self.buttons[message["b"]], message["d"])
        elif kind == "scroll":
            self.mouse.scroll(message["dx"], message["dy"])
        elif kind == "key":
            name = message["k"]
            key = self.keys.get(name, name.lower())
            self._press(self.keyboard, self.held_keys, key, message["d"])
        elif kind == "combo":
            aliases = {"Win": "Meta", "Ctrl": "Control", "Esc": "Escape"}
            self.release_all()
            if sys.platform == "win32" and message["keys"] == ["Win", "L"]:
                import ctypes
                if active() and not ctypes.windll.user32.LockWorkStation():
                    raise OSError("O sistema recusou o bloqueio.")
                return
            pressed = []
            try:
                for name in message["keys"]:
                    if not active():
                        break
                    if name == "PrintScreen":
                        key = self.keyboard_module.Key.print_screen
                    else:
                        key = self.keys.get(aliases.get(name, name), name.lower())
                    self._press(self.keyboard, self.held_keys, key, "down")
                    pressed.append(key)
                    # O Windows precisa ver o modificador pressionado antes da próxima tecla.
                    await asyncio.sleep(COMBO_STEP)
            finally:
                # Solta na ordem inversa, como uma pessoa faria (Tab antes de Alt).
                for key in reversed(pressed):
                    try:
                        self._press(self.keyboard, self.held_keys, key, "up")
                    except Exception as exc:
                        event("input_release_failed", exc)
                self.release_all()
        elif kind == "text":
            for char in message["s"]:
                if not active():
                    break
                if char in "\n\r\t":
                    key = self.keys["Tab" if char == "\t" else "Enter"]
                    self.keyboard.tap(key)
                elif sys.platform == "win32":
                    send_unicode(char)
                else:
                    self.keyboard.type(char)
                await asyncio.sleep(0)

    def release_all(self):
        failed = False
        for controller, held in ((self.mouse, self.held_buttons), (self.keyboard, self.held_keys)):
            for value in held:
                try:
                    controller.release(value)
                except Exception as exc:
                    event("input_release_failed", exc)
                    failed = True
            held.clear()
        if failed:
            print("AVISO: o sistema recusou a liberação de uma tecla ou botão.", flush=True)
