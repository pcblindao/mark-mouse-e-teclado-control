import os
import re
import subprocess
import sys
from codecs import BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from markcontrol.gui import autostart


def _repository_files():
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--deduplicate", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    )
    for entry in filter(None, paths.split(b"\0")):
        relative = os.fsdecode(entry)
        path = root / relative
        if path.is_file():
            yield relative, path


def test_product_name_across_repository():
    test_file = Path(__file__).resolve()
    old_name = re.compile(rb"teclado[ -]e[ -]mouse", re.IGNORECASE)
    matches = []
    for relative, path in _repository_files():
        if path == test_file:
            continue
        if old_name.search(path.read_bytes()):
            matches.append(relative)
    assert not matches, "Nome antigo em " + ", ".join(matches)


def test_short_product_name_is_absent_from_repository_text():
    short_name = b"Mark" + b" Control"
    matches = []
    for relative, path in _repository_files():
        data = path.read_bytes()
        if data.startswith((BOM_UTF32_BE, BOM_UTF32_LE)):
            data = data.decode("utf-32", errors="replace").encode("utf-8")
        elif data.startswith((BOM_UTF16_BE, BOM_UTF16_LE)):
            data = data.decode("utf-16", errors="replace").encode("utf-8")
        elif b"\0" in data:
            continue
        if short_name in data:
            matches.append(relative)
    assert not matches, "Nome curto em " + ", ".join(matches)


def test_windows_autostart_migrates_and_removes_both_names(monkeypatch):
    old_name = "Mark Teclado e Mouse Control"
    new_name = "Mark Mouse e Teclado Control"
    values = {old_name: "old.exe"}
    command = [r"C:\Program Files\Mark Mouse e Teclado Control.exe", "--autostart"]

    def query_value(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def set_value(key, name, reserved, kind, data):
        values[name] = data

    def delete_value(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *args: nullcontext(object()),
        CreateKeyEx=lambda *args: nullcontext(object()),
        QueryValueEx=query_value, SetValueEx=set_value, DeleteValue=delete_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(autostart, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(autostart, "command", lambda: command)

    assert autostart.enabled()
    autostart.set_enabled(True)
    assert values == {new_name: subprocess.list2cmdline(command)}
    assert autostart.enabled()

    values[old_name] = "old.exe"
    values[new_name] = ""
    assert autostart.enabled()
    autostart.set_enabled(False)
    assert values == {}
    assert not autostart.enabled()

    values[old_name] = "old.exe"
    autostart.set_enabled(False)
    assert values == {}
    autostart.set_enabled(False)
