import asyncio
import io
import threading
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
    app = Mock(active=active, headless=False, pending=False)
    app.root.state.return_value = window
    app.config.last_good = {}
    state = dict(u.snapshot(app), version="", stage="")
    assert state["active"] is active and state["window"] == window
    u.restore(app, state)
    assert app.toggle_agent.call_count == int(active)
    assert app.root.withdraw.call_count == int(window == "withdrawn")
    assert app.root.iconify.call_count == int(window == "iconic")


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
