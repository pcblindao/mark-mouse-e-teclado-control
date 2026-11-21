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
    monkeypatch.setattr(install, "product_port_busy", lambda: False)
    monkeypatch.setattr(install, "setup_running", lambda: False)
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


def test_official_070_is_a_proven_legacy_binary():
    assert install.LEGACY["910b4c993cd6fdab032cdca29400a99c7bcd645d4abddeb5d6a0bbd7c6ab7867"] == ("0.7.0", 26983829)


def test_unknown_product_port_blocks_a_new_desktop_without_terminating_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_instance", None)
    monkeypatch.setattr(install, "product_port_busy", lambda: True)
    terminate = Mock(side_effect=AssertionError("Unknown process must never be terminated"))
    monkeypatch.setattr(install, "stop_legacy", terminate)
    with pytest.raises(RuntimeError, match="porta de controle"):
        install.acquire_desktop(tmp_path)
    probe = install.FileLock(tmp_path / "desktop-instance.lock")
    assert probe.acquire()
    probe.close()
    assert install._instance is None
    terminate.assert_not_called()


def test_existing_ipc_owner_is_reused_even_while_its_service_port_is_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "_instance", None)
    port_check = Mock(side_effect=AssertionError("Port probe before single-instance handoff"))
    monkeypatch.setattr(install, "product_port_busy", port_check)
    owner = install.Instance(tmp_path)
    try:
        assert owner.acquire()
        owner.ready = True
        assert install.acquire_desktop(tmp_path).exit_code == 0
        assert owner.events.get(timeout=1) == "open"
        port_check.assert_not_called()
    finally:
        owner.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_setup_mutex_blocks_normal_start_but_allows_explicit_post_install_handoff(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "setup_running", lambda: True)
    assert install.bootstrap([]).exit_code == 0
    assert not target.exists()
    assert_prefs(profile, expected)
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    monkeypatch.setattr(sys, "executable", str(target))
    from markcontrol.gui import autostart
    monkeypatch.setattr(autostart, "enabled", lambda: False)
    startup = install.bootstrap([], allow_setup=True)
    try:
        assert startup.exit_code is None and startup.instance is not None
    finally:
        startup.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")
def test_canonical_start_holds_install_lock_until_desktop_lock_is_acquired(tmp_path, monkeypatch):
    profile, _, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    monkeypatch.setattr(sys, "executable", str(target))
    from markcontrol.gui import autostart
    monkeypatch.setattr(autostart, "enabled", lambda: False)
    original = install.Instance.acquire

    def check_lock(instance):
        probe = install.FileLock(profile / "desktop-install.lock")
        assert not probe.acquire(), "Installer must not slip into the startup gap"
        return original(instance)

    monkeypatch.setattr(install.Instance, "acquire", check_lock)
    startup = install.bootstrap([])
    try:
        assert startup.instance is not None
        probe = install.FileLock(profile / "desktop-install.lock")
        assert probe.acquire()
        probe.close()
    finally:
        startup.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_prepare_and_register_migrate_only_proven_aliases_and_preserve_preferences(tmp_path, monkeypatch):
    profile, expected, source, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    old = b"official legacy fixture"
    target.write_bytes(old)
    install.mark_installed(target, "0.6.0")
    alias = tmp_path / "previous-download.exe"
    alias.write_bytes(old)
    important = tmp_path / "unrelated.exe"
    important.write_bytes(b"unrelated file")
    checksum = hashlib.sha256(old).hexdigest()
    monkeypatch.setattr(install, "LEGACY", {checksum: ("0.6.0", len(old))})
    monkeypatch.setattr(install, "stop_owner", Mock())
    monkeypatch.setattr(install, "stop_legacy", Mock(return_value=[alias, important]))
    from markcontrol.gui import autostart
    register = Mock()
    monkeypatch.setattr(autostart, "enabled", lambda: True)
    monkeypatch.setattr(autostart, "set_enabled", register)
    assert install.prepare_install() == 0
    assert target.read_bytes() == old
    assert_prefs(profile, expected)
    target.write_bytes(source.read_bytes())  # Simulates Inno [Files], not another app.
    monkeypatch.setattr(sys, "executable", str(target))
    assert install.register_install() == 0
    assert install.installed_info(target)["version"] == install.__version__
    assert alias.read_bytes() == source.read_bytes()
    assert important.read_bytes() == b"unrelated file"
    assert not (profile / "desktop-install-pending.json").exists()
    register.assert_called_once_with(True)
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_prepare_falls_back_only_to_proven_legacy_shutdown(tmp_path, monkeypatch):
    profile, expected, _, _ = fixture_install(tmp_path, monkeypatch)
    owner = Mock(side_effect=[TimeoutError("old IPC unresponsive"), None, None])
    legacy = Mock(side_effect=[[], [], []])
    monkeypatch.setattr(install, "stop_owner", owner)
    monkeypatch.setattr(install, "stop_legacy", legacy)
    assert install.prepare_install() == 0
    assert owner.call_count == 3 and legacy.call_count == 3
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_prepare_refuses_unidentified_owner_without_starting_a_second_copy(tmp_path, monkeypatch):
    profile, expected, _, _ = fixture_install(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "stop_owner", Mock(side_effect=RuntimeError("unknown owner")))
    monkeypatch.setattr(install, "stop_legacy", Mock(return_value=[]))
    launch = Mock()
    monkeypatch.setattr(install, "launch", launch)
    with pytest.raises(RuntimeError, match="unknown owner"):
        install.prepare_install()
    assert_prefs(profile, expected)
    assert not (profile / "desktop-install-pending.json").exists()
    launch.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_prepare_rejects_occupied_unknown_port_and_canonical_helper(tmp_path, monkeypatch):
    profile, expected, _, target = fixture_install(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "stop_owner", Mock())
    monkeypatch.setattr(install, "product_port_busy", lambda: True)
    with pytest.raises(RuntimeError, match="porta"):
        install.prepare_install()
    monkeypatch.setattr(sys, "executable", str(target))
    with pytest.raises(RuntimeError, match="auxiliar temporário"):
        install.prepare_install()
    assert_prefs(profile, expected)


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_register_refuses_noncanonical_execution(tmp_path, monkeypatch):
    profile, expected, _, target = fixture_install(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="fora da pasta"):
        install.register_install()
    assert not target.exists()
    assert_prefs(profile, expected)


def test_source_cli_obeys_the_same_single_instance_handoff(monkeypatch):
    from markcontrol import cli
    from markcontrol.gui import app
    monkeypatch.setattr(cli, "setup", Mock())
    monkeypatch.setattr(cli, "prepare_desktop", Mock())
    monkeypatch.setattr(install, "setup_running", lambda: False)
    monkeypatch.setattr(install, "_instance", None)
    startup = SimpleNamespace(exit_code=0, close=Mock())
    monkeypatch.setattr(install, "acquire_desktop", Mock(return_value=startup))
    launch = Mock(side_effect=AssertionError("Second GUI opened"))
    monkeypatch.setattr(app, "launch", launch)
    assert cli.main([]) == 0
    startup.close.assert_called_once()
    launch.assert_not_called()


def test_prepare_waits_for_existing_update_or_session_lease(tmp_path):
    from markcontrol.updater import lease
    with lease(directory=tmp_path):
        assert not install.updates_idle(tmp_path)
    assert install.updates_idle(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows Setup helper")
def test_older_setup_never_downgrades_newer_installed_binary(tmp_path, monkeypatch):
    profile, expected, _, target = fixture_install(tmp_path, monkeypatch)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"newer installation")
    install.mark_installed(target, "0.8.0")
    stop = Mock()
    monkeypatch.setattr(install, "stop_owner", stop)
    with pytest.raises(ValueError, match="mais nova"):
        install.prepare_install()
    stop.assert_not_called()
    assert target.read_bytes() == b"newer installation"
    assert_prefs(profile, expected)
