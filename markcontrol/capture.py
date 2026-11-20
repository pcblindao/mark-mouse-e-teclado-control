import hashlib
from io import BytesIO

import mss
from PIL import Image


def monitor_count():
    with mss.mss() as screen:
        return len(screen.monitors) - 1


def frame(monitor, quality, previous, force):
    with mss.mss() as screen:
        count = len(screen.monitors) - 1
        if not count:
            raise RuntimeError("Nenhum monitor disponível.")
        monitor = monitor if monitor <= count else 1
        region = screen.monitors[monitor]
        shot = screen.grab(region)

    pixels = shot.bgra
    stamp = hashlib.blake2s(pixels, digest_size=16).digest()
    identity = (monitor, count, tuple(region.items()), quality, stamp)
    if not force and identity == previous:
        return monitor, count, region, identity, None
    with Image.frombytes("RGB", shot.size, pixels, "raw", "BGRX") as image:
        with BytesIO() as buffer:
            image.save(buffer, "JPEG", quality=quality)
            return monitor, count, region, identity, buffer.getvalue()
