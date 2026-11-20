import asyncio
import sys

from .security import NAMED_KEYS


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

    def _move(self, message, region):
        self.mouse.position = (
            region["left"] + round(message["x"] * (region["width"] - 1)),
            region["top"] + round(message["y"] * (region["height"] - 1)),
        )

    @staticmethod
    def _press(controller, held, value, direction):
        if direction == "down" and value not in held:
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
            try:
                for name in message["keys"]:
                    if not active():
                        break
                    if name == "PrintScreen":
                        key = self.keyboard_module.Key.print_screen
                    else:
                        key = self.keys.get(aliases.get(name, name), name.lower())
                    self._press(self.keyboard, self.held_keys, key, "down")
            finally:
                self.release_all()
        elif kind == "text":
            for char in message["s"]:
                if not active():
                    break
                if char in "\n\r\t":
                    key = self.keys["Tab" if char == "\t" else "Enter"]
                    self.keyboard.tap(key)
                else:
                    self.keyboard.type(char)
                await asyncio.sleep(0)

    def release_all(self):
        failed = False
        for controller, held in ((self.mouse, self.held_buttons), (self.keyboard, self.held_keys)):
            for value in held:
                try:
                    controller.release(value)
                except Exception:
                    failed = True
            held.clear()
        if failed:
            print("AVISO: o sistema recusou a liberação de uma tecla ou botão.", flush=True)
