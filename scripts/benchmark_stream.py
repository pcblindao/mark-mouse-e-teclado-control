"""Synthetic, interleaved microbenchmarks; never capture the desktop or network."""
import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from markcontrol.client import prepare_frame


def legacy_prepare(data, target):
    # Exact image path used by 0.5.1, retained here only for comparison.
    with Image.open(BytesIO(data), formats=["JPEG"]) as frame:
        if frame.width * frame.height > 32_000_000:
            raise ValueError("Quadro grande demais.")
        if target is None or (frame.width <= target[0] and frame.height <= target[1]):
            return frame.convert("RGB")
        scale = min(target[0] / frame.width, target[1] / frame.height)
        size = tuple(max(1, round(n * scale)) for n in frame.size)
        image = frame.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        return image if image.mode == "RGB" else image.convert("RGB")


def paired(before, after, iterations):
    values = [[], []]
    for index in range(iterations + 2):
        for which in ((0, 1) if index % 2 else (1, 0)):
            started = time.perf_counter()
            result = (before, after)[which]()
            elapsed = (time.perf_counter() - started) * 1000
            if isinstance(result, Image.Image):
                result.close()
            if index >= 2:
                values[which].append(elapsed)
    return {"before_median_ms": round(statistics.median(values[0]), 3),
            "after_median_ms": round(statistics.median(values[1]), 3)}


def synthetic(size):
    image = Image.new("RGB", size, "#142331")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 28):
        draw.rectangle((0, y, size[0], y + 22), fill="#233747" if y % 56 else "#192b3c")
        draw.text((20, y + 4), "Mark Mouse e Teclado Control - transmission benchmark - 0123456789 " * 15, fill="#c4e9f1")
    return image


async def command_delay(data, threaded):
    loop = asyncio.get_running_loop()
    received = loop.create_future()

    def command():
        time.sleep(0.005)
        sent = time.perf_counter()
        loop.call_soon_threadsafe(lambda: received.set_result((time.perf_counter() - sent) * 1000))

    sender = threading.Thread(target=command)
    sender.start()
    if threaded:
        image = await asyncio.to_thread(prepare_frame, data, (1600, 900))
    else:
        image = legacy_prepare(data, (1600, 900))
    image.close()
    delay = await received
    sender.join()
    return delay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    results = {"scope": "synthetic processing and event-loop delay; excludes screen capture, display, LAN/Wi-Fi and hardware input", "cases": []}
    for size in ((1920, 1080), (3840, 2160)):
        image = synthetic(size)
        raw = image.convert("RGBA").tobytes()
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=85, subsampling=0, optimize=False)
        data = buffer.getvalue()
        row = {"resolution": list(size), "jpeg_bytes": len(data)}
        row["change_detection"] = paired(lambda: hashlib.blake2s(raw, digest_size=16).digest(),
                                         lambda: hashlib.sha256(raw).digest(), args.iterations)
        row["decode_and_fit"] = paired(lambda: legacy_prepare(data, (1600, 900)),
                                       lambda: prepare_frame(data, (1600, 900)), args.iterations)
        delays = [[], []]
        for index in range(args.iterations):
            for threaded in ((False, True) if index % 2 else (True, False)):
                delays[int(threaded)].append(asyncio.run(command_delay(data, threaded)))
        row["command_event_loop_delay"] = {
            "before_median_ms": round(statistics.median(delays[0]), 3),
            "after_median_ms": round(statistics.median(delays[1]), 3),
            "before_max_ms": round(max(delays[0]), 3), "after_max_ms": round(max(delays[1]), 3)}
        results["cases"].append(row)
        image.close()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
