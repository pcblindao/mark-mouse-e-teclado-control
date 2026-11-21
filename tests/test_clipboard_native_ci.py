"""Exercise only synthetic clipboard data on disposable Windows CI machines."""
import asyncio
import os
from io import BytesIO

import pytest
from PIL import Image

from markcontrol import clipboard


@pytest.mark.skipif(os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true",
                    reason="Real clipboard is touched only on disposable Windows CI")
def test_native_windows_text_image_and_files(tmp_path):
    async def roundtrip():
        active = lambda: True
        try:
            text = clipboard.ClipboardPayload("text", "Teste isolado — Olá 🖼️")
            await clipboard.write_payload(text, active)
            assert await clipboard.read_payload(active) == text

            image = Image.new("RGBA", (11, 7), (20, 90, 180, 130))
            buffer = BytesIO()
            image.save(buffer, "PNG")
            await clipboard.write_payload(clipboard.ClipboardPayload("image", buffer.getvalue()), active)
            received = await clipboard.read_payload(active)
            assert received.kind == "image"
            with Image.open(BytesIO(received.value)) as decoded:
                assert decoded.convert("RGBA").tobytes() == image.tobytes()

            file = tmp_path / "arquivo de teste.txt"
            file.write_text("Dados sintéticos", encoding="utf-8")
            payload = clipboard.ClipboardPayload("files", (str(file.resolve()),))
            await clipboard.write_payload(payload, active)
            assert await clipboard.read_payload(active) == payload
        finally:
            await clipboard.write_payload(clipboard.ClipboardPayload("text", ""), active)
    asyncio.run(roundtrip())
