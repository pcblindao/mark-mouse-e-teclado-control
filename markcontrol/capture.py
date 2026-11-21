import hashlib
import time
from io import BytesIO

import mss
from PIL import Image


def monitor_count():
    with mss.mss() as screen:
        return len(screen.monitors) - 1


def output_size(width, height, max_height=0):
    if not max_height or height <= max_height:
        return width, height
    return max(1, round(width * max_height / height)), max_height


class StreamCapture:
    """Own the capture device on one worker thread for the whole session."""
    def __init__(self):
        self.screen = None
        self.refresh_at = 0

    def close(self):
        if self.screen is not None:
            self.screen.close()
            self.screen = None

    def frame(self, monitor, quality, previous, force, max_height=0):
        if self.screen is None or time.monotonic() >= self.refresh_at:
            self.close()
            self.screen = mss.mss()
            self.refresh_at = time.monotonic() + 2
        screen = self.screen
        count = len(screen.monitors) - 1
        if not count:
            raise RuntimeError("Nenhum monitor disponível.")
        monitor = monitor if monitor <= count else 1
        region = screen.monitors[monitor]
        shot = screen.grab(region)
        pixels = shot.bgra
        stamp = hashlib.sha256(pixels).digest()
        identity = (monitor, count, tuple(region.items()), quality, max_height, stamp)
        if not force and identity == previous:
            return monitor, count, region, identity, None
        with Image.frombytes("RGB", shot.size, pixels, "raw", "BGRX") as image:
            size = output_size(*image.size, max_height)
            resized = image.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0) if size != image.size else None
            try:
                with BytesIO() as buffer:
                    (resized or image).save(buffer, "JPEG", quality=quality, subsampling=0, optimize=False)
                    return monitor, count, region, identity, buffer.getvalue()
            finally:
                if resized is not None:
                    resized.close()


def frame(monitor, quality, previous, force):
    source = StreamCapture()
    try:
        return source.frame(monitor, quality, previous, force)
    finally:
        source.close()
