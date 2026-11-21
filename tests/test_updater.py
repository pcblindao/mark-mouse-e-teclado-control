import asyncio
import base64
import io
import json
import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from markcontrol import updater as u


def signed(key, data=b"binary", version="0.6.0", **changes):
    digest, size = u.fingerprint(io.BytesIO(data))
    fields = dict(version=version, name=u.asset(), sha256=digest, size=size)
    fields.update(changes)
    fields["signature"] = key.sign(u.canonical(fields)).hex()
    return u.canonical(fields)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(u, "__version__", "0.5.0")
    secret = Ed25519PrivateKey.generate()
    monkeypatch.setattr(u, "PUBLIC_KEY_HEX", secret.public_key().public_bytes_raw().hex())
    return secret


@pytest.mark.parametrize("version", ["0.4.99", "0.5.0"])
def test_refuses_old_or_equal(key, version):
    with pytest.raises(ValueError):
        u.manifest(signed(key, version=version), version, u.asset())


def test_signature_and_identity(key):
    with pytest.raises(InvalidSignature):
        u.manifest(signed(Ed25519PrivateKey.generate()), "0.6.0", u.asset())
    for raw in (signed(key, name="outro"), signed(key, version="0.7.0"), signed(key, size=True)):
        with pytest.raises(ValueError):
            u.manifest(raw, "0.6.0", u.asset())


def test_content_and_size(key):
    fields = u.manifest(signed(key), "0.6.0", u.asset())
    u.verify(io.BytesIO(b"binary"), fields)
    for data in (b"BINARY", b"bin"):
        with pytest.raises(ValueError):
            u.verify(io.BytesIO(data), fields)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{}", b'{"signature":0}', b"x" * 4097])
def test_malformed_manifests_are_rejected(key, raw):
    with pytest.raises(ValueError):
        u.manifest(raw, "0.6.0", u.asset())


def test_duplicate_manifest_fields_are_rejected(key):
    raw = signed(key)
    raw = raw[:-1] + b',"size":6}'
    with pytest.raises(ValueError, match="Campo repetido"):
        u.manifest(raw, "0.6.0", u.asset())


def test_portable_executable_is_eligible_for_automatic_updates(tmp_path, monkeypatch):
    target = tmp_path / "Downloads" / "Mark Mouse e Teclado Control.exe"
    monkeypatch.setattr(u.sys, "frozen", True, raising=False)
    monkeypatch.setattr(u.sys, "executable", str(target))
    monkeypatch.setattr(u.platform, "machine", lambda: "AMD64")
    assert u.installed() == target.resolve()


@pytest.mark.parametrize("tampered", [False, True])
def test_prepare_validates_download_before_starting_helper(tmp_path, monkeypatch, key, tampered):
    monkeypatch.setattr(u, "data_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(u, "sys", SimpleNamespace(platform="win32"))
    target = tmp_path / "app.exe"
    target.write_bytes(b"old")
    name, version = u.asset(), "0.6.0"
    base = f"https://github.com/{u.REPO}/releases/download/v{version}/"
    release = dict(tag_name="v" + version, draft=False, prerelease=False,
                   assets=[dict(name=n, browser_download_url=base + n) for n in (name, name + ".sig")])
    def small(url, limit, check):
        check()
        return json.dumps(release).encode() if url == u.API else signed(key)
    monkeypatch.setattr(u, "small", small)
    monkeypatch.setattr(u, "read_url", lambda url, limit, out, check: out.write(b"tampered" if tampered else b"binary"))
    spawn = Mock()
    monkeypatch.setattr(u, "spawn", spawn)
    if tampered:
        with pytest.raises(ValueError):
            u.prepare(target, threading.Event())
        spawn.assert_not_called()
        assert not list(u.pool(target).glob("release-*"))
    else:
        stage, process = u.prepare(target, threading.Event())
        assert process is spawn.return_value
        assert (stage / name).read_bytes() == b"binary"
        assert (u.pool(target) / "previous.exe").read_bytes() == b"old"
        spawn.assert_called_once()
    assert target.read_bytes() == b"old"


def test_cancelled_update_never_reads_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "data_dir", lambda: tmp_path)
    cancel = threading.Event()
    cancel.set()
    network = Mock(side_effect=lambda url, limit, checkpoint: checkpoint())
    monkeypatch.setattr(u, "small", network)
    with pytest.raises(InterruptedError):
        u.prepare(tmp_path / "app.exe", cancel)


def test_failed_check_retries_and_does_not_close_the_app(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(u, "event", lambda *args: events.append(args))
    monitor = u.Monitor(tmp_path / "app.exe", False)
    failed = Future()
    failed.set_exception(OSError("offline"))
    monitor.future = failed
    app = Mock()
    app.config.last_good = {"auto_update": True}
    now = time.monotonic()
    try:
        monitor.tick(app)
        assert now + 59 <= monitor.next_check <= time.monotonic() + 61
        assert events[0][0] == "update_failed"
        app.close.assert_not_called()
    finally:
        monitor.stop()


def test_release_signer_accepts_ci_secret_without_exposing_it(tmp_path, monkeypatch, key, capsys):
    from scripts import sign_release
    target = tmp_path / u.asset()
    target.write_bytes(b"release test artifact")
    private = base64.b64encode(key.private_bytes_raw()).decode()
    monkeypatch.setenv("MARKCONTROL_TEST_SIGNING_KEY", private)
    monkeypatch.setattr(sign_release.sys, "argv", ["sign_release.py", str(target), "--key-env", "MARKCONTROL_TEST_SIGNING_KEY"])
    sign_release.main()
    raw = target.with_name(target.name + ".sig").read_bytes()
    fields = u.manifest(raw, sign_release.__version__, target.name)
    u.verify(io.BytesIO(target.read_bytes()), fields)
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err


def test_both_session_entry_points(tmp_path, monkeypatch):
    from markcontrol import client, server

    async def connected(*args):
        with pytest.raises(u.sqlite3.OperationalError), u.lease(exclusive=True, directory=tmp_path):
            pytest.fail("Troca admitida durante a conexão")

    for module, entry, inner, args in (
        (client, client.Client.connect, "_connect", (None, False)),
        (server, server.Agent.websocket, "control_socket", (None,)),
    ):
        monkeypatch.setattr(module, "session_lease", lambda: u.lease(directory=tmp_path))
        handler = Mock()
        setattr(handler, inner, connected)
        asyncio.run(entry(handler, *args))
        with u.lease(exclusive=True, directory=tmp_path):
            pass


def test_session_defers_download(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "data_dir", lambda: tmp_path)
    checked = threading.Event()
    blocked = threading.Event()
    cancel = threading.Event()
    with patch.object(cancel, "wait", side_effect=lambda _: blocked.set() or checked.wait(3)), \
            patch.object(u, "small", side_effect=lambda url, limit, check: check() or b'{"draft":true,"tag_name":"v1"}') as fetch:
        with u.lease(), u.lease():
            thread = threading.Thread(target=u.prepare, args=(tmp_path / "app", cancel))
            thread.start()
            assert blocked.wait(2)
            assert thread.is_alive()
        checked.set()
        thread.join(3)
        assert not thread.is_alive()
        assert fetch.call_count == 1


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("window", ["normal", "withdrawn", "iconic"])
def test_restore_state(active, window):
    app = Mock(active=active, desired_active=active, headless=False, pending=False)
    app.root.state.return_value = window
    app.config.last_good = {}
    state = dict(u.snapshot(app), version="", stage="")
    assert state["active"] is active and state["window"] == window
    u.restore(app, state)
    assert app.toggle_agent.call_count == int(active)
    assert app.root.withdraw.call_count == int(window == "withdrawn")
    assert app.root.iconify.call_count == int(window == "iconic")


def test_restore_restarts_requested_active_after_temporary_listener_failure():
    app = Mock(active=False, desired_active=True, headless=True, pending=False)
    app.config.last_good = {}
    app.root.state.return_value = "withdrawn"
    state = dict(u.snapshot(app), version="", stage="")
    assert state["active"] is False and state["desired_active"] is True
    u.restore(app, state)
    app.toggle_agent.assert_called_once_with(persist=False)
    assert app.desired_active is True


def test_restore_does_not_activate_a_configuration_awaiting_review():
    app = Mock(active=False, desired_active=True, headless=False, pending=False)
    app.config.last_good = {"review_required": True}
    state = dict(active=True, desired_active=True, window="normal", version="", stage="")
    u.restore(app, state)
    app.toggle_agent.assert_not_called()
    assert app.desired_active is False


@pytest.mark.parametrize("failure", ["timeout", "spawn", "exit", None])
def test_replace_and_restore(tmp_path, monkeypatch, failure):
    target = tmp_path / "app"
    previous = tmp_path / "previous"
    stage = tmp_path / "stage"
    stage.mkdir()
    target.write_bytes(b"old")
    previous.write_bytes(b"old")
    (stage / u.asset()).write_bytes(b"new")
    states = []
    proc = Mock()
    proc.poll.return_value = 1 if failure == "exit" else None

    def launch(executable, args, extra_env, **kw):
        assert executable == target and args == ["agent"]
        state = u.json.loads(extra_env[u.RESUME])
        assert state["active"] and state["window"] == "withdrawn"
        states.append((target.read_bytes(), state["version"]))
        if len(states) == 1:
            if failure == "spawn":
                raise OSError("launch")
            if failure is None:
                (stage / "ready").write_text("0.6.0")
        return proc

    def wait(predicate, seconds):
        assert seconds == 30
        return predicate()

    monkeypatch.setattr(u, "wait_for", wait)
    monkeypatch.setattr(u, "spawn", launch)
    monkeypatch.setattr(u, "stop_candidate", Mock())
    state = dict(active=True, headless=False, window="withdrawn", argv=["agent"])
    if failure == "spawn":
        with pytest.raises(OSError):
            u.swap(target, previous, stage, state, "0.6.0")
    else:
        u.swap(target, previous, stage, state, "0.6.0")
    assert states == ([(b"new", "0.6.0"), (b"old", "")] if failure else [(b"new", "0.6.0")])
    assert previous.read_bytes() == b"old"
    assert target.read_bytes() == (b"old" if failure else b"new")
