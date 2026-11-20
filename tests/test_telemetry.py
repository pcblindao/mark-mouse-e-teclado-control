import asyncio
import copy
from unittest.mock import Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from markcontrol import telemetry
from markcontrol.gui import app
from services import diagnostics_receiver as receiver


@pytest.mark.parametrize("choice", [True, False, None])
def test_first_use_never_assumes_consent(monkeypatch, choice):
    from types import SimpleNamespace
    ui = SimpleNamespace(done=Mock(), root=Mock(), status=Mock())
    ui.done.is_set.return_value = False
    monkeypatch.setattr(telemetry, "preference", lambda: None)
    save = Mock()
    monkeypatch.setattr(telemetry, "set_preference", save)
    monkeypatch.setattr(app.messagebox, "askyesnocancel", Mock(return_value=choice))
    app.App.diagnostic_consent(ui)
    if choice is None:
        save.assert_not_called()
    else:
        save.assert_called_once_with(choice)


def failure():
    return {"event": "tray_failed", "error_type": "RuntimeError",
            "frames": [{"file": "tray.py", "function": "run", "line": 40}]}


def test_no_consent_means_no_queue_or_network(tmp_path):
    transport = Mock()
    telemetry.enqueue(failure(), tmp_path)
    assert not (tmp_path / "diagnostic-pending.json").exists()
    assert not telemetry.send_pending(tmp_path, "https://example.com", transport)
    transport.assert_not_called()


def test_retry_keeps_queue_until_receiver_confirms(tmp_path):
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue(failure(), tmp_path)
    bad = Mock(side_effect=OSError("offline"))
    with pytest.raises(OSError):
        telemetry.send_pending(tmp_path, "https://example.com", bad)
    good = Mock()
    assert telemetry.send_pending(tmp_path, "https://example.com", good)
    assert bad.call_args.args[0]["report_id"] == good.call_args.args[0]["report_id"]
    assert not telemetry.pending(tmp_path)
    assert not telemetry.send_pending(tmp_path, "https://example.com", good)
    assert good.call_count == 1


def test_revoke_clears_pending_and_keeps_new_errors_local(tmp_path):
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue(failure(), tmp_path)
    telemetry.set_preference(False, tmp_path)
    telemetry.enqueue(failure(), tmp_path)
    assert not (tmp_path / "diagnostic-pending.json").exists()
    assert telemetry.preference(tmp_path) is False


def test_queue_bounded_and_new_events_during_send_are_preserved(tmp_path):
    telemetry.set_preference(True, tmp_path)
    for _ in range(60):
        telemetry.enqueue(failure(), tmp_path)
    assert len(telemetry.pending(tmp_path)) == 50
    telemetry.send_pending(tmp_path, "https://example.com",
                           lambda *_: telemetry.enqueue(failure(), tmp_path))
    assert len(telemetry.pending(tmp_path)) == 1


def test_allowlist_discards_content_and_identifiers():
    record = dict(failure(), pin="12345678", ip="192.168.1.2", message="clipboard", hostname="private")
    assert telemetry.clean_record(record) == failure()
    assert telemetry.clean_record({"event": "typed_password"}) is None


def test_receiver_validation_dedup_and_durable_receipts(tmp_path):
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue(failure(), tmp_path)
    captured = []
    telemetry.send_pending(tmp_path, "https://example.com", lambda payload, _: captured.append(payload))
    payload, = captured
    signature = receiver.validate(payload)
    store = receiver.Store(tmp_path / "reports.sqlite")
    store.accept(payload, signature)
    store.accept(payload, signature)
    another = dict(payload, report_id="b" * 64)
    store.accept(another, receiver.validate(another))
    assert store.db.execute("SELECT count(*), sum(occurrences) FROM reports").fetchone() == (1, 2)
    store.db.close()
    reopened = receiver.Store(tmp_path / "reports.sqlite")
    assert reopened.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 2
    reopened.db.close()
    bad = copy.deepcopy(payload)
    bad["events"][0]["clipboard"] = "secret"
    with pytest.raises(ValueError):
        receiver.validate(bad)


def test_receiver_http_rejects_unapproved_fields_and_accepts_durable_report(tmp_path):
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue(failure(), tmp_path)
    captured = []
    telemetry.send_pending(tmp_path, "https://example.com", lambda payload, _: captured.append(payload))

    async def run():
        client = TestClient(TestServer(receiver.create_app(tmp_path / "http.sqlite", "")))
        await client.start_server()
        try:
            response = await client.post(receiver.PATH, json=captured[0])
            assert response.status == 202
            assert await response.json() == {"accepted": captured[0]["report_id"]}
            response = await client.post(receiver.PATH, json=dict(captured[0], secret="not allowed"))
            assert response.status == 400
        finally:
            await client.close()
    asyncio.run(run())
