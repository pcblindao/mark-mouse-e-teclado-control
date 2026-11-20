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


@pytest.mark.parametrize("headless", [False, True])
def test_minimized_session_stays_in_tray(monkeypatch, headless):
    # Uso doméstico: a sessão não cria aviso nenhum na tela; só recolhe para a bandeja.
    ui = app.App.__new__(app.App)
    ui.root, ui.tray, ui.shell = Mock(), Mock(), Mock()
    ui.render_update = Mock()
    ui.headless, ui.active = headless, True
    ui.root.state.return_value = "withdrawn"
    ui.tray.ready.is_set.return_value = True
    window = Mock()
    monkeypatch.setattr(app.tk, "Toplevel", window)
    ui.session("192.168.1.2")
    ui.root.title.assert_called_with(app.TITLE)
    ui.tray.update.assert_called_once_with(True, "192.168.1.2")
    ui.tray.icon.notify.assert_not_called()
    ui.hide()
    ui.root.withdraw.assert_called_once()
    ui.session("")
    ui.root.deiconify.assert_not_called()
    ui.root.attributes.assert_not_called()
    ui.root.bell.assert_not_called()
    window.assert_not_called()
    assert ui.render_update.call_count == (0 if headless else 2)


def test_session_with_no_tray_can_minimize():
    ui = app.App.__new__(app.App)
    ui.root, ui.tray, ui.shell = Mock(), Mock(), Mock()
    ui.render_update = Mock()
    ui.headless, ui.active = False, True
    ui.tray.ready.is_set.return_value = False
    ui.session("192.168.1.2")
    ui.hide()
    ui.root.iconify.assert_called_once()
    ui.root.withdraw.assert_not_called()


@pytest.mark.parametrize("attached", [0, 1, 2])
def test_windows_identity_precedes_tk(monkeypatch, attached):
    dll = Mock()

    def processes(pid, size):
        if attached == 1:
            app.ctypes.cast(pid, app.ctypes.POINTER(app.ctypes.c_ulong))[0] = app.os.getpid()
        return attached

    dll.kernel32.GetConsoleProcessList.side_effect = processes
    dll.kernel32.GetConsoleWindow.return_value = 1234
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.ctypes, "windll", dll, raising=False)
    monkeypatch.setattr(app, "enable_dpi", lambda: None)

    def create_root(**kwargs):
        dll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("Marcos.MarkMouseTecladoControl")
        assert kwargs["className"] == app.TITLE
        if attached == 1:
            hwnd, mode = dll.user32.ShowWindow.call_args.args
            assert (hwnd.value, mode) == (1234, 0)
        else:
            dll.user32.ShowWindow.assert_not_called()
        raise StopIteration

    monkeypatch.setattr(app.tk, "Tk", create_root)
    with pytest.raises(StopIteration):
        app.launch(None, False, False)


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
