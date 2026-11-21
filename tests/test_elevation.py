import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from markcontrol import elevation, updater
from markcontrol.gui import autostart
from markcontrol.security import decode_message
from markcontrol.server import Agent, Control


def detector(monkeypatch, probe, elevated=False):
    monkeypatch.setattr(elevation, "supported", lambda: True)
    now = [100.0]
    found = elevation.BlockDetector(elevated=elevated, probe=probe, clock=lambda: now[0], interval=5)
    return found, now


def test_blocked_window_is_reported_once_per_interval(monkeypatch):
    found, now = detector(monkeypatch, lambda point: True)
    assert found.check((1, 2))
    assert not found.check((1, 2))
    now[0] += 5
    assert found.check(None)


def test_elevated_agent_or_unknown_window_reports_nothing(monkeypatch):
    assert not detector(monkeypatch, lambda point: True, elevated=True)[0].check((1, 2))
    assert not detector(monkeypatch, lambda point: None)[0].check((1, 2))
    assert not detector(monkeypatch, lambda point: False)[0].check((1, 2))

    def broken(point):
        raise TypeError("ctypes")
    assert not detector(monkeypatch, broken)[0].check((1, 2))


def test_task_runs_elevated_at_logon_without_prompt():
    xml = elevation.task_xml("S-1-5-21-1", r"C:\Apps & Co\Mark.exe", ["--autostart"], r"C:\Apps & Co")
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert xml.count("<UserId>S-1-5-21-1</UserId>") == 2
    assert r"<Command>C:\Apps &amp; Co\Mark.exe</Command>" in xml
    assert "<Arguments>--autostart</Arguments>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml


def test_source_runs_are_not_forced_to_elevate(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not elevation.required()


def test_elevated_windows_autostart_uses_scheduled_task(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(autostart, "task_mode", lambda: True)
    monkeypatch.setattr(elevation, "register_task", lambda: calls.append("register"))
    monkeypatch.setattr(elevation, "delete_task", lambda: calls.append("delete"))
    monkeypatch.setattr(autostart, "remove_run_entries", lambda: calls.append("run-removed"))
    autostart.set_enabled(True)
    autostart.set_enabled(False)
    assert calls == ["register", "run-removed", "delete", "run-removed"]


def test_agent_warns_controller_about_admin_window():
    async def run():
        control = Control(Mock(alive=lambda: True), AsyncMock(), Mock(), 1)
        control.region = {"left": 0, "top": 0, "width": 100, "height": 100}
        agent = Agent.__new__(Agent)
        agent.control = control
        agent.input = Mock(apply=AsyncMock(), point=Mock(return_value=(50, 50)))
        agent.allow_clipboard = agent.allow_files = True
        agent.blocked = Mock(check=Mock(return_value=True))
        click = decode_message(json.dumps({"t": "click", "x": 0.5, "y": 0.5, "b": "left", "d": "down"}))
        await agent.handle(control, click)
        control.ws.send_json.assert_awaited_with({"t": "notice", "code": "admin_window"})
        agent.blocked.check.assert_called_once_with((50, 50))
        agent.input.apply.assert_awaited_once()

        agent.blocked.check.reset_mock()
        await agent.handle(control, decode_message(json.dumps({"t": "move", "x": 0.1, "y": 0.1})))
        agent.blocked.check.assert_not_called()
    asyncio.run(run())


def test_update_from_unelevated_version_reopens_as_admin(monkeypatch):
    app = SimpleNamespace(done=Mock(is_set=lambda: False), close=Mock())
    monkeypatch.setattr(elevation, "required", lambda: True)
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    relaunch = Mock(return_value=True)
    monkeypatch.setattr(elevation, "relaunch", relaunch)
    updater.elevate(app, ["--autostart"])
    relaunch.assert_called_once_with(["--autostart"])
    app.close.assert_called_once()

    relaunch.return_value = False
    app.close.reset_mock()
    updater.elevate(app, [])
    app.close.assert_not_called()


def test_every_toolbar_shortcut_passes_client_validation():
    from markcontrol.gui.viewer import COMBOS as MENU
    from markcontrol.security import COMBOS
    client = SimpleNamespace(send=Mock())
    from markcontrol.client import Client
    for keys in MENU.values():
        assert keys in COMBOS, keys
        Client.send_combo(client, list(keys))
    assert client.send.call_count == len(MENU)
    assert {tuple(keys) for keys in MENU.values()} == {tuple(keys) for keys in COMBOS}


def test_combo_holds_modifiers_and_releases_in_reverse(monkeypatch):
    from markcontrol import inputctl
    from markcontrol.inputctl import Input
    monkeypatch.setattr(inputctl, "COMBO_STEP", 0)
    device = Input.__new__(Input)
    device.keys = {"Control": "ctrl", "Shift": "shift", "Escape": "esc", "Alt": "alt", "Tab": "tab",
                   "Meta": "cmd", "F4": "f4"}
    device.held_keys, device.held_buttons = set(), set()
    device.mouse = Mock()
    log = []
    device.keyboard = Mock(press=lambda k: log.append(("down", k)), release=lambda k: log.append(("up", k)))
    for keys, names in ((["Ctrl", "Shift", "Esc"], ["ctrl", "shift", "esc"]), (["Alt", "Tab"], ["alt", "tab"]),
                        (["Win", "D"], ["cmd", "d"]), (["Alt", "F4"], ["alt", "f4"])):
        log.clear()
        asyncio.run(device.apply(decode_message(json.dumps({"t": "combo", "keys": keys})), None, lambda: True))
        assert log == [("down", n) for n in names] + [("up", n) for n in reversed(names)]
        assert not device.held_keys
