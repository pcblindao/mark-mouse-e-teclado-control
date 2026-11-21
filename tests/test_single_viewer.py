import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from markcontrol.discovery import Peer
from markcontrol.gui import app


def fixture(monkeypatch):
    peer = Peer("192.168.1.2", "PC", 47801, ":".join(["ab"] * 32), False, False)
    ui = app.App.__new__(app.App)
    ui.peers, ui.viewers, ui.root = {peer.fingerprint: (0, peer)}, {}, Mock()
    ui.warning, ui.status = Mock(), Mock()
    ui.config = Mock()
    ui.config.refresh.return_value = {"clipboard": False}
    created = []

    class Window:
        def __init__(self, root, peer, cfg):
            self.peer = peer
            self.client = SimpleNamespace(closed=threading.Event())
            self.destroyed = False
            self.winfo_exists = lambda: not self.destroyed
            self.deiconify, self.lift, self.release, self.connect, self.title = (Mock() for _ in range(5))
            self.callbacks = {}
            created.append(self)

        def bind(self, event, callback, **kwargs):
            self.callbacks[event] = callback

    monkeypatch.setattr(app, "Viewer", Window)
    return ui, peer, created, Window


def test_repeated_open_and_busy_own_session_reuse_same_viewer(monkeypatch):
    ui, peer, created, _ = fixture(monkeypatch)
    ui.connect(peer.fingerprint)
    ui.connect(peer.fingerprint)
    ui.peers[peer.fingerprint] = (1, replace(peer, busy=True))
    ui.connect(peer.fingerprint)
    assert len(created) == 1
    assert created[0].deiconify.call_count == 2
    assert created[0].lift.call_count == 2
    created[0].connect.assert_not_called()
    ui.status.set.assert_not_called()


def test_disconnected_viewer_reconnects_using_latest_peer_and_preferences(monkeypatch):
    ui, peer, created, _ = fixture(monkeypatch)
    ui.connect(peer.fingerprint)
    window = created[0]
    window.client.closed.set()
    latest = replace(peer, ip="192.168.1.9", name="Renamed PC")
    ui.peers[peer.fingerprint] = (1, latest)
    ui.connect(peer.fingerprint)
    assert len(created) == 1
    assert window.peer is latest and window.local_clip is False
    window.release.assert_called_once()
    window.connect.assert_called_once_with()
    window.deiconify.assert_called_once()


def test_destroy_children_or_old_viewer_cannot_remove_current_viewer(monkeypatch):
    ui, peer, created, _ = fixture(monkeypatch)
    ui.connect(peer.fingerprint)
    first = created[0]
    first.callbacks["<Destroy>"](SimpleNamespace(widget=Mock()))
    assert ui.viewers[peer.fingerprint] is first
    first.destroyed = True
    ui.connect(peer.fingerprint)
    second = created[1]
    first.callbacks["<Destroy>"](SimpleNamespace(widget=first))
    assert ui.viewers[peer.fingerprint] is second
    second.callbacks["<Destroy>"](SimpleNamespace(widget=second))
    assert peer.fingerprint not in ui.viewers


def test_failed_and_reentrant_construction_do_not_duplicate_or_stick(monkeypatch):
    ui, peer, created, factory = fixture(monkeypatch)
    def opening(root, found, cfg):
        ui.connect(peer.fingerprint)
        return factory(root, found, cfg)
    monkeypatch.setattr(app, "Viewer", opening)
    ui.connect(peer.fingerprint)
    assert len(created) == 1
    ui.viewers.clear()
    monkeypatch.setattr(app, "Viewer", Mock(side_effect=RuntimeError("fixture")))
    with pytest.raises(RuntimeError):
        ui.connect(peer.fingerprint)
    assert peer.fingerprint not in ui.viewers


def test_different_peers_can_have_distinct_viewers(monkeypatch):
    ui, peer, created, _ = fixture(monkeypatch)
    other = replace(peer, fingerprint=":".join(["cd"] * 32), ip="192.168.1.3")
    ui.peers[other.fingerprint] = (0, other)
    ui.connect(peer.fingerprint)
    ui.connect(other.fingerprint)
    assert len(created) == 2 and len(ui.viewers) == 2
