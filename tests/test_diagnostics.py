import logging
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
from types import SimpleNamespace

from markcontrol import diagnostics
from markcontrol.gui import app


def test_error_log_excludes_exception_text_and_local_variables(tmp_path):
    logger = diagnostics.logger
    handlers, level = logger.handlers[:], logger.level
    try:
        logger.handlers = [logging.NullHandler()]
        diagnostics.setup(tmp_path)
        try:
            raise ValueError("PIN=12345678 secret clipboard 192.168.1.2")
        except ValueError as exc:
            diagnostics.event("test_failure", exc)
        content = (tmp_path / "logs" / "diagnostics.log").read_text()
        assert "test_failure" in content and "ValueError" in content
        assert "test_diagnostics.py" in content
        for secret in ("12345678", "clipboard", "192.168.1.2", str(tmp_path)):
            assert secret not in content
        handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
        assert handler.maxBytes == 256 * 1024 and handler.backupCount == 3
        details = diagnostics.report(tmp_path)
        url = urlsplit(diagnostics.issue_url(details))
        assert url.netloc == "github.com"
        assert "test_failure" in parse_qs(url.query)["body"][0]
    finally:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers, logger.level = handlers, level


def test_unwritable_log_folder_does_not_stop_app(tmp_path):
    (tmp_path / "logs").write_text("file instead of directory")
    diagnostics.setup(tmp_path)


def test_report_is_bounded_and_tolerates_missing_logs(tmp_path):
    assert "Nenhum evento" in diagnostics.report(tmp_path)
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "diagnostics.log").write_text("window_hidden_in_tray\n" * 10000)
    assert len(diagnostics.report(tmp_path)) < 2000


def test_failed_notification_cannot_block_minimizing(monkeypatch):
    order = []
    ui = SimpleNamespace(root=Mock(), tray=Mock(), session_ip="", notified=False)
    ui.root.withdraw.side_effect = lambda: order.append("hidden")
    ui.tray.ready.is_set.return_value = True
    ui.tray.icon.HAS_NOTIFICATION = True

    def fail(*args):
        order.append("notification")
        raise RuntimeError("tray error")

    ui.tray.icon.notify.side_effect = fail
    modal = Mock()
    monkeypatch.setattr(app.messagebox, "showinfo", modal)
    app.App.hide(ui)
    assert order == ["hidden"]
    modal.assert_not_called()
    ui.root.deiconify.assert_not_called()


def test_missing_notifications_still_hides_window(monkeypatch):
    ui = SimpleNamespace(root=Mock(), tray=Mock(), session_ip="", notified=False)
    ui.tray.ready.is_set.return_value = True
    ui.tray.icon.HAS_NOTIFICATION = False
    modal = Mock()
    monkeypatch.setattr(app.messagebox, "showinfo", modal)
    app.App.hide(ui)
    ui.root.withdraw.assert_called_once()
    modal.assert_not_called()
