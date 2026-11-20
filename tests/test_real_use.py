import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from markcontrol import client, server
from markcontrol.gui import app
from markcontrol.gui.viewer import Viewer
from markcontrol.security import Authorization


@pytest.mark.parametrize("ip", ["127.8.0.2", "::1", "192.168.100.103", "::ffff:192.168.100.103"])
def test_local_login_requires_development_switch(monkeypatch, ip):
    class Request(dict):
        content_type = "application/json"

        async def json(self, loads):
            return loads("{}")

    agent = SimpleNamespace(control=None, auth=Authorization(lambda body: {}))
    request = Request(peer=ip)
    monkeypatch.setattr(server, "local_addresses", lambda: {"192.168.100.103": None})
    for value in (None, "0", "true", "1"):
        if value is None:
            monkeypatch.delenv("MARKCONTROL_DEV_LOOPBACK", raising=False)
        else:
            monkeypatch.setenv("MARKCONTROL_DEV_LOOPBACK", value)
        response = asyncio.run(server.Agent.login(agent, request))
        body = json.loads(response.body)
        if value == "1":
            assert response.status == 200 and agent.auth.claim(ip, body["token"]).alive()
        else:
            assert response.status == 403 and body == {"error": "Não dá para controlar o próprio PC"}
            assert agent.auth.pending is None


def test_strip_lifecycle(monkeypatch):
    ui = app.App.__new__(app.App)
    ui.root, ui.runner, ui.tray = Mock(), Mock(), Mock()
    ui.banner, ui.banner_text, ui.render_update = Mock(), Mock(), Mock()
    ui.headless, ui.active, ui.strip, ui.shell = False, True, None, Mock()
    strip = Mock()
    strip.winfo_reqwidth.return_value = 400
    strip.winfo_screenwidth.return_value = 1000
    window, button, caption = Mock(return_value=strip), Mock(), Mock()
    monkeypatch.setattr(app.tk, "Toplevel", window)
    monkeypatch.setattr(app, "Button", button)
    monkeypatch.setattr(app, "label", caption)
    ui.session("192.168.1.2")
    ui.hide()
    ui.hide()
    window.assert_called_once()
    strip.attributes.assert_called_once_with("-topmost", True)
    strip.geometry.assert_called_once_with("+300+0")
    strip.protocol.assert_called_once_with("WM_DELETE_WINDOW", ui.runner.stop)
    strip.deiconify.assert_called_once()
    ui.root.withdraw.assert_called()
    caption.assert_called_once_with(strip, "Controlado por 192.168.1.2 · Ctrl+Alt+Shift+Q", color="white")
    ui.tray.update.assert_called_once_with(True, "192.168.1.2")
    stop, reopen = [call.args[2] for call in button.call_args_list]
    assert stop == ui.runner.stop
    ui.root.reset_mock()
    reopen()
    ui.root.deiconify.assert_called_once()
    strip.destroy.assert_called_once()
    ui.hide()
    ui.session("")
    assert strip.destroy.call_count == 2 and ui.strip is None
    ui.tray.update.assert_called_with(True, "")


def test_native_frame_keeps_pixels(monkeypatch):
    data = BytesIO()
    Image.new("RGB", (200, 100)).save(data, "JPEG")
    monkeypatch.setattr(Image.Image, "resize", lambda *a, **kw: pytest.fail("reamostragem"))
    for target in (None, (200, 100), (300, 120)):
        assert client.prepare_frame(data.getvalue(), target).size == (200, 100)


def test_pointer_after_scrolling():
    ui = SimpleNamespace(photo=True, buttons=set(), canvas=Mock(), client=Mock(), item=1)
    ui.canvas.bbox.return_value = (0, 0, 3440, 1440)
    ui.canvas.canvasx.side_effect = lambda x: x + 1800
    ui.canvas.canvasy.side_effect = lambda y: y + 400
    Viewer.pointer(ui, SimpleNamespace(x=100, y=80), "move")
    ui.client.send.assert_called_once_with(dict(t="move", x=1900/3439, y=480/1439))
