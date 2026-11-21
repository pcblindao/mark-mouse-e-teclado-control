import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
PRODUCT_NAME = "Mark Mouse e Teclado Control"
# Nome legado usado somente para ler e remover o valor do registro.
LEGACY_RUN_NAME = "Mark Teclado " "e Mouse Control"


def desktop_file():
    return Path.home() / ".config" / "autostart" / "markcontrol.desktop"


def command():
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(exe), "--autostart"]
    if os.name == "nt":
        exe = exe.with_name("pythonw.exe")
    return [str(exe), "-m", "markcontrol", "--autostart"]


def quote(arg):
    # O leitor desfaz primeiro os escapes de string e depois as aspas de Exec.
    escaped = "".join("\\" + c if c in ('\\', '"', '$', '`') else c for c in arg)
    value = escaped.replace("%", "%%").replace("\\", "\\\\")
    value = value.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    return '"' + value + '"'


def task_mode():
    """No Windows publicado o início automático é a tarefa elevada do Agendador."""
    from .. import elevation
    return os.name == "nt" and elevation.required() and elevation.is_elevated()


def remove_run_entries():
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as key:
        for name in (PRODUCT_NAME, LEGACY_RUN_NAME):
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, name)


def enabled():
    if os.name != "nt":
        return desktop_file().exists()
    from .. import elevation
    if elevation.required():
        with suppress(OSError):
            if elevation.task_exists():
                return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as key:
            for name in (PRODUCT_NAME, LEGACY_RUN_NAME):
                with suppress(FileNotFoundError):
                    if winreg.QueryValueEx(key, name)[0]:
                        return True
    except FileNotFoundError:
        return False
    return False


def set_enabled(value):
    if os.name == "nt" and task_mode():
        from .. import elevation
        if value:
            elevation.register_task()
        else:
            elevation.delete_task()
        remove_run_entries()
    elif os.name == "nt":
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as key:
            if value:
                winreg.SetValueEx(key, PRODUCT_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command()))
            names_to_remove = (LEGACY_RUN_NAME,) if value else (PRODUCT_NAME, LEGACY_RUN_NAME)
            for name in names_to_remove:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, name)
    elif value:
        path = desktop_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = " ".join(map(quote, command()))
        path.write_text(f"[Desktop Entry]\nType=Application\nName={PRODUCT_NAME}\nExec={cmd}\nTerminal=false\n",
                        encoding="utf-8")
    else:
        desktop_file().unlink(missing_ok=True)
