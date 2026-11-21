import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from markcontrol import installation as install


def test_only_one_instance_and_authenticated_fixed_actions(tmp_path, monkeypatch):
    first, second = install.Instance(tmp_path), install.Instance(tmp_path)
    try:
        assert first.acquire()
        assert not second.acquire()
        monkeypatch.setattr(install, "_instance", first)
        assert install.notify(tmp_path, "status") == {"accepted": True, "ready": False}
        install.mark_ready()
        assert install.notify(tmp_path, "open") == {"accepted": True, "ready": True}
        assert list(install.actions()) == ["open"]
        assert install.notify(tmp_path, "exec") is None
        state = json.loads((tmp_path / "desktop-instance.json").read_text())
        with socket.create_connection(("127.0.0.1", state["port"]), timeout=1) as connection:
            connection.sendall(json.dumps({"nonce": "0" * 64, "action": "shutdown"}).encode() + b"\n")
            assert json.loads(connection.recv(1024))["accepted"] is False
        assert list(install.actions()) == []
        assert install.notify(tmp_path, "shutdown")["accepted"] is True
        assert list(install.actions()) == ["shutdown"]
    finally:
        first.close()
        second.close()
    assert second.acquire()
    second.close()


@pytest.mark.parametrize("args", [["--help"], ["agent", "--help"], ["scan"], ["--apply-update"]])
def test_cli_utilities_never_install_or_acquire_desktop_lock(tmp_path, monkeypatch, args):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(install, "data_dir", Mock(side_effect=AssertionError("profile touched")))
    startup = install.bootstrap(args)
    assert startup.exit_code is None and startup.instance is None


def test_source_execution_never_installs(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(install, "data_dir", Mock(side_effect=AssertionError("profile touched")))
    assert install.bootstrap([]).instance is None


def prefs(folder):
    folder.mkdir()
    contents = {
        "config.json": b'{"agent_enabled":true,"auto_update":true}',
        "diagnostic-consent.json": b'{"version":2,"enabled":true}',
        "identity.pem": b"existing identity fixture",
        "trust.json": b'{"remembered":"existing fixture"}',
        "history.json": b"[]",
    }
    for name, content in contents.items():
        (folder / name).write_bytes(content)
    return {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}


def assert_prefs(folder, expected):
    assert {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in expected} == expected


def fixture_install(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    expected = prefs(profile)
    source, target = tmp_path / "download.exe", tmp_path / "Programs" / "MarkControl" / install.CANONICAL_NAME
    source.write_bytes(b"new desktop executable fixture")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(source))
    monkeypatch.setattr(install, "__version__", "0.7.0")
    monkeypatch.setattr(install, "data_dir", lambda: profile)
    monkeypatch.setattr(install, "canonical_path", lambda: target)
    monkeypatch.setattr(install, "stop_legacy", Mock(return_value=[]))
    monkeypatch.delenv(install.RESUME, raising=False)
    monkeypatch.delenv("MARKCONTROL_INSTALL_HANDOFF", raising=False)
    return profile, expected, source, target


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_install_replaces_existing_and_preserves_every_preference_byte(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"older installed fixture")
    install.mark_installed(target, "0.6.0")
    child = install.Instance(profile)
    calls = []
    def launch(path, argv, **kwargs):
        calls.append((path, argv, kwargs))
        assert child.acquire()
        child.ready = True
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(install, "launch", launch)
    try:
        startup = install.bootstrap([])
        assert startup.exit_code == 0
        assert target.read_bytes() == source.read_bytes()
        assert (target.parent / ".bootstrap-previous.exe").read_bytes() == b"older installed fixture"
        assert install.installed_info(target)["version"] == "0.7.0"
        assert len(calls) == 1 and calls[0][2]["handoff"] is True
        assert_prefs(profile, expected)
    finally:
        child.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_old_download_never_downgrades_a_newer_installation(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"newer installed fixture")
    install.mark_installed(target, "0.8.0")
    launch = Mock()
    monkeypatch.setattr(install, "launch", launch)
    assert install.bootstrap([]).exit_code == 0
    assert target.read_bytes() == b"newer installed fixture"
    assert install.installed_info(target)["version"] == "0.8.0"
    launch.assert_called_once_with(target.resolve(), [], handoff=True)
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_update_resume_keeps_same_process_path_for_old_updater_ack(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    monkeypatch.setenv(install.RESUME, '{"version":"0.7.0","active":true}')
    launch = Mock(side_effect=AssertionError("candidate was relocated"))
    monkeypatch.setattr(install, "launch", launch)
    startup = install.bootstrap([])
    try:
        assert startup.exit_code is None and startup.instance is not None
        assert not target.exists()
        assert install.RESUME in os.environ
        assert_prefs(profile, expected)
    finally:
        startup.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_unknown_existing_file_is_not_overwritten(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"unidentified important file")
    with pytest.raises(ValueError, match="não pôde ser identificado"):
        install.bootstrap([])
    assert target.read_bytes() == b"unidentified important file"
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_failed_new_install_restores_old_binary_and_metadata(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"healthy old fixture")
    install.mark_installed(target, "0.6.0")
    launch = Mock(return_value=SimpleNamespace(poll=lambda: 1))
    monkeypatch.setattr(install, "launch", launch)
    with pytest.raises(RuntimeError, match="não confirmou"):
        install.bootstrap([])
    assert target.read_bytes() == b"healthy old fixture"
    assert install.installed_info(target)["version"] == "0.6.0"
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_second_open_reuses_the_existing_instance_without_starting_another(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    install.mark_installed(target, "0.7.0")
    child = install.Instance(profile)
    launch = Mock(side_effect=AssertionError("a second copy was launched"))
    monkeypatch.setattr(install, "launch", launch)
    try:
        assert child.acquire()
        child.ready = True
        assert install.bootstrap([]).exit_code == 0
        assert child.events.get(timeout=1) == "open"
        assert_prefs(profile, expected)
    finally:
        child.close()
