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
    new_ids = []
    def during_send(*_):
        telemetry.enqueue(failure(), tmp_path)
        new_ids.append(telemetry.pending(tmp_path)[-1]["id"])
    telemetry.send_pending(tmp_path, "https://example.com", during_send)
    assert len(telemetry.pending(tmp_path)) == 25
    assert new_ids[0] in {row["id"] for row in telemetry.pending(tmp_path)}
    telemetry.send_pending(tmp_path, "https://example.com", lambda *_: None)
    telemetry.send_pending(tmp_path, "https://example.com", lambda *_: None)
    assert not telemetry.pending(tmp_path)


def test_allowlist_discards_content_and_identifiers():
    record = dict(failure(), pin="12345678", ip="192.168.1.2", message="clipboard", hostname="private")
    assert telemetry.clean_record(record) == failure()
    assert telemetry.clean_record({"event": "typed_password"}) is None


def test_new_diagnostic_scope_requires_fresh_consent(tmp_path):
    from markcontrol.config import write_json
    write_json(tmp_path / "diagnostic-consent.json", {"version": 1, "enabled": True})
    assert telemetry.preference(tmp_path) is None
    telemetry.enqueue({"event": "session_started"}, tmp_path)
    assert not (tmp_path / "diagnostic-pending.json").exists()


def test_normal_session_logs_arrive_without_an_exception(tmp_path):
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue({"event": "session_started"}, tmp_path)
    telemetry.enqueue({"event": "viewer_stats", "metrics": {"fps": 42, "decode_ms": 7,
                      "width": 1920, "height": 1080, "clipboard": "private", "rtt_ms": True}}, tmp_path)
    captured = []
    assert telemetry.send_pending(tmp_path, "https://example.com", lambda value, _: captured.append(value))
    payload = captured[0]
    assert payload["events"][1]["metrics"] == {"fps": 42, "decode_ms": 7, "width": 1920, "height": 1080}
    assert receiver.validate(payload)
    assert not telemetry.pending(tmp_path)


def test_bad_pending_rows_cannot_block_future_logs(tmp_path):
    from markcontrol.config import write_json
    telemetry.set_preference(True, tmp_path)
    write_json(tmp_path / "diagnostic-pending.json", [{"bad": "row"}, 1, None])
    telemetry.enqueue(failure(), tmp_path)
    assert len(telemetry.pending(tmp_path)) == 1
    assert telemetry.send_pending(tmp_path, "https://example.com", lambda *_: None)


def test_receiver_publishes_latest_activity_without_duplicate_issue(tmp_path, monkeypatch):
    import json
    telemetry.set_preference(True, tmp_path)
    telemetry.enqueue({"event": "session_ended"}, tmp_path)
    captured = []
    telemetry.send_pending(tmp_path, "https://example.com", lambda value, _: captured.append(value))
    payload = captured[0]
    calls = []
    def github(token, method, path, value=None):
        calls.append((method, path, value))
        if method == "GET":
            return []
        return {"number": 10}
    monkeypatch.setattr(receiver, "github", github)
    signature = receiver.validate(payload)
    number = receiver.publish("test-token", signature, json.dumps(payload))
    assert number == 10
    assert receiver.publish("test-token", signature, json.dumps(payload), number, 2) == number
    assert [method for method, *_ in calls] == ["GET", "POST", "PATCH"]
    assert "Lotes recebidos neste grupo: 2" in calls[-1][2]["body"]


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
