import asyncio

import pyperclip

MAX_BYTES = 100 * 1024


class ClipboardError(ValueError):
    pass


def check_text(value):
    if not isinstance(value, str):
        raise ClipboardError("Texto inválido.")
    if len(value) > MAX_BYTES:
        raise ClipboardError("Texto acima de 100 KiB.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ClipboardError("UTF-8 inválido.") from None
    if size > MAX_BYTES:
        raise ClipboardError("Texto acima de 100 KiB.")
    return size


def _transfer(active, value):
    try:
        if not active():
            raise ClipboardError("Sessão encerrada.")
        if value is None:
            value = pyperclip.paste()
        else:
            pyperclip.copy(value)
        return value, check_text(value)
    except (pyperclip.PyperclipException, OSError, ValueError):
        raise ClipboardError("Área de transferência indisponível ou texto inválido.") from None


async def read_text(active):
    return await asyncio.to_thread(_transfer, active, None)


async def write_text(value, active):
    check_text(value)
    _, size = await asyncio.to_thread(_transfer, active, value)
    return size
