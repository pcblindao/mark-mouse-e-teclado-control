import threading
from concurrent.futures import Future
from queue import SimpleQueue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from markcontrol.config import Config
from markcontrol.gui import app
from markcontrol.gui.tray import Tray


def make_app(tmp_path):
    ui = app.App.__new__(app.App)
    ui.config = Config(tmp_path)
    ui.done = threading.Event()
    ui.pending = ui.active = ui.desired_active = False
    ui.headless = False
    ui.args = object()
    ui.runner = SimpleNamespace(start=Mock(), stop=Mock(), events=SimpleQueue(), thread=None)
    ui.root, ui.tray, ui.toggle, ui.enabled = Mock(), Mock(), Mock(), Mock()
    ui.status, ui.warning = Mock(), Mock()
    ui.session_ip = ""
    ui.chat_messages = []
    ui.chat_text = ui.chat_window = ui.chat_feedback = None
    ui.update_monitor = None
    ui.retry_activation_at = float("inf")
    ui.render_activation = ui.render_update = ui.show_public_network = ui.update_list = Mock()
    return ui


def test_active_is_persisted_and_exit_preserves_choice(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    register = Mock()
    monkeypatch.setattr(app.autostart, "set_enabled", register)
    app.App.toggle_agent(ui)
    assert ui.desired_active and ui.pending
    assert Config(tmp_path).read()["agent_enabled"] is True
    register.assert_called_once_with(True)
    ui.runner.start.assert_called_once_with(ui.args)
    app.App.close(ui)
    assert Config(tmp_path).read()["agent_enabled"] is True
    assert ui.done.is_set()


def test_user_can_cancel_activation_and_explicit_off_is_persisted(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    ui.config.set_agent_enabled(True)
    ui.desired_active = ui.pending = True
    register = Mock()
    monkeypatch.setattr(app.autostart, "set_enabled", register)
    app.App.toggle_agent(ui)
    assert not ui.desired_active and not ui.pending
    assert Config(tmp_path).read()["agent_enabled"] is False
    ui.runner.stop.assert_called_once()
    ui.runner.start.assert_not_called()
    register.assert_called_once_with(False)


def test_failed_preference_write_does_not_start_agent_or_change_autostart(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    ui.config.set_agent_enabled = Mock(side_effect=OSError("read only"))
    register, log = Mock(), Mock()
    monkeypatch.setattr(app.autostart, "set_enabled", register)
    monkeypatch.setattr(app, "event", log)
    app.App.toggle_agent(ui)
    assert not ui.desired_active and not ui.pending
    ui.runner.start.assert_not_called()
    register.assert_not_called()
    assert log.call_args.args[0] == "config_failed"


def test_autostart_error_does_not_lose_persisted_active_choice(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    monkeypatch.setattr(app.autostart, "set_enabled", Mock(side_effect=OSError("registry")))
    monkeypatch.setattr(app, "event", Mock())
    app.App.toggle_agent(ui)
    assert Config(tmp_path).read()["agent_enabled"] is True
    ui.runner.start.assert_called_once()
    ui.warning.set.assert_called_once()


def test_unexpected_stop_retries_without_changing_persisted_preferences(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    ui.headless = ui.active = ui.desired_active = True
    ui.config.set_agent_enabled(True)
    clock = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    register = Mock()
    monkeypatch.setattr(app.autostart, "set_enabled", register)
    ui.runner.events.put(("off", ""))
    app.App.poll(ui)
    assert ui.retry_activation_at == 105 and not ui.active and not ui.done.is_set()
    clock[0] = 104.9
    app.App.poll(ui)
    ui.runner.start.assert_not_called()
    clock[0] = 105
    app.App.poll(ui)
    ui.runner.start.assert_called_once_with(ui.args)
    assert ui.pending and Config(tmp_path).read()["agent_enabled"] is True
    register.assert_not_called()


@pytest.mark.parametrize("configured,desired,auto,registry,want_start,want_hidden", [
    (True, False, True, True, False, True),
    (True, True, True, True, True, True),
    (True, True, False, True, True, False),
    (False, False, True, True, True, True),
    (False, False, False, True, False, False),
])
def test_launch_respects_explicit_disabled_state_and_legacy_autostart(
        monkeypatch, configured, desired, auto, registry, want_start, want_hidden):
    root = Mock()
    ui = Mock()
    ui.config.last_good = {}
    ui.desired_active = desired
    ui.activation_configured = configured
    ui.update_monitor = None
    ui.runner.thread = None
    constructor = Mock(return_value=ui)
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app.tk, "Tk", Mock(return_value=root))
    monkeypatch.setattr(app, "enable_dpi", Mock())
    monkeypatch.setattr(app, "App", constructor)
    monkeypatch.setattr(app.updater, "installed", lambda: None)
    monkeypatch.setattr(app.autostart, "enabled", lambda: registry)
    monkeypatch.setattr(app.telemetry, "Sender", Mock())
    monkeypatch.delenv(app.updater.RESUME, raising=False)
    app.launch(object(), False, auto)
    assert constructor.call_args.args[2] is want_hidden
    assert ui.toggle_agent.call_count == int(want_start)
    if want_start:
        ui.toggle_agent.assert_called_once_with(persist=False)


def test_config_migration_and_preference_save_preserve_explicit_off(tmp_path):
    cfg = Config(tmp_path)
    legacy = cfg.defaults()
    del legacy["agent_enabled"]
    from markcontrol.config import write_json
    write_json(cfg.path, legacy)
    assert "agent_enabled" not in cfg.read()
    cfg.set_agent_enabled(False)
    cfg.save("PC", True, 90, files=False)
    fresh = Config(tmp_path).read()
    assert fresh["agent_enabled"] is False and fresh["files"] is False


def test_config_recovery_requires_review_before_enabling(tmp_path):
    cfg = Config(tmp_path)
    from markcontrol.config import write_json
    write_json(cfg.path, dict(cfg.defaults(), review_required=True))
    with pytest.raises(ValueError):
        cfg.set_agent_enabled(True)
    assert cfg.read()["agent_enabled"] is False
    cfg.set_agent_enabled(False)
    cfg.save("PC", True, 85)
    cfg.set_agent_enabled(True)
    assert cfg.read()["agent_enabled"] is True


def test_tray_action_tracks_requested_activation_while_session_indicator_is_preserved():
    tray = Tray.__new__(Tray)
    tray.active, tray.desired_active = False, True
    assert tray.toggle_label == "Desativar este PC"
    tray.active, tray.desired_active = True, False
    assert tray.toggle_label == "Ativar este PC"
    tray.ready = threading.Event()
    tray.ready.set()
    tray.icon = Mock()
    tray.images = ["normal", "active-session"]
    tray.update(False, "192.168.1.2")
    assert tray.icon.icon == "active-session"
    assert "Sessão de controle ativa" in tray.icon.title
    tray.icon.notify.assert_not_called()


def test_chat_reception_is_bounded_and_never_opens_a_window(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    popup = Mock()
    monkeypatch.setattr(app.tk, "Toplevel", popup)
    for number in range(25):
        app.App.receive_chat(ui, {"text": str(number), "reply": Mock()})
    for bad in (None, {}, {"text": "", "reply": Mock()}, {"text": "x", "reply": None}):
        app.App.receive_chat(ui, bad)
    assert len(ui.chat_messages) == 20
    assert ui.chat_messages[0]["text"] == "5"
    popup.assert_not_called()


@pytest.mark.parametrize("delivered", [True, False])
def test_chat_marks_delivery_only_after_future_confirmation(tmp_path, monkeypatch, delivered):
    ui = make_app(tmp_path)
    ui.headless = True
    ui.session_ip = "192.168.1.2"
    future = Future()
    callback = Mock(return_value=future)
    item = {"text": "Mensagem", "reply": callback}
    ui.chat_messages.append(item)
    popup = Mock()
    monkeypatch.setattr(app.tk, "Toplevel", popup)
    app.App.reply_chat(ui, "Resposta")
    assert "answer" not in item and item["reply"] is None
    future.set_result(delivered)
    assert "answer" not in item
    app.App.poll(ui)
    if delivered:
        assert item["answer"] == "Resposta" and item["reply"] is None
    else:
        assert "answer" not in item and item["reply"] is callback
        ui.warning.set.assert_called_once()
    popup.assert_not_called()


def test_chat_does_not_reenable_reply_after_session_ended(tmp_path, monkeypatch):
    ui = make_app(tmp_path)
    future = Future()
    item = {"text": "Mensagem", "reply": Mock(return_value=future)}
    ui.chat_messages.append(item)
    app.App.reply_chat(ui, "Resposta")
    monkeypatch.setattr(app, "event", Mock())
    future.set_exception(OSError("connection closed"))
    kind, value = ui.runner.events.get_nowait()
    assert kind == "chat-delivery"
    app.App.chat_delivered(ui, value)
    assert item["reply"] is None and "answer" not in item
