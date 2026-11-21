"""Modo administrador no Windows.

O Windows (UIPI) descarta em silêncio cliques e teclas que um processo comum
envia para janelas de processos elevados: Gerenciador de Tarefas, instaladores,
Editor do Registro e programas abertos com "Executar como administrador".
Para controlá-las, o agente precisa rodar elevado. Este módulo:

- detecta se o processo atual está elevado;
- detecta se a janela sob o cursor/em foco pertence a um processo elevado;
- reabre o aplicativo elevado (UAC) quando ele é aberto sem privilégio;
- registra a tarefa agendada que inicia o aplicativo elevado no logon, sem
  novos prompts do UAC.

Nenhuma função aqui envia entradas ou altera janelas.
"""

import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Mark Mouse e Teclado Control"
WAIT_PREVIOUS = "--wait-previous"

TOKEN_QUERY = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_ELEVATION = 20
GA_ROOT = 2
ERROR_ACCESS_DENIED = 5
CREATE_NO_WINDOW = 0x08000000


def supported():
    return os.name == "nt"


# --- Elevação de processos ---------------------------------------------------------

def _token_elevated(process):
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        # Um processo comum não consegue ler o token de um processo elevado.
        return True if ctypes.get_last_error() == ERROR_ACCESS_DENIED else None
    try:
        value, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi.GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(value),
                                          ctypes.sizeof(value), ctypes.byref(size)):
            return None
        return bool(value.value)
    finally:
        kernel.CloseHandle(token)


def is_elevated():
    if not supported():
        return False
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    return bool(_token_elevated(kernel.GetCurrentProcess()))


def window_elevated(hwnd):
    """True/False para a janela informada; None quando não dá para saber."""
    if not supported() or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    pid = wintypes.DWORD()
    if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return None
    process = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        # Processos protegidos (UAC, Winlogon) também recusam a consulta.
        return True if ctypes.get_last_error() == ERROR_ACCESS_DENIED else None
    try:
        return _token_elevated(process)
    finally:
        kernel.CloseHandle(process)


def window_at(x, y):
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    hwnd = user.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return user.GetAncestor(hwnd, GA_ROOT) if hwnd else None


def foreground_window():
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    return user.GetForegroundWindow()


class BlockDetector:
    """Indica quando a entrada vai para uma janela que o Windows bloqueia."""

    def __init__(self, elevated=None, probe=None, clock=time.monotonic, interval=5.0):
        self.elevated = is_elevated() if elevated is None else elevated
        self.probe = probe or self._probe
        self.clock = clock
        self.interval = interval
        self.last = None

    @staticmethod
    def _probe(point):
        hwnd = window_at(*point) if point is not None else foreground_window()
        return window_elevated(hwnd)

    def check(self, point=None):
        """Retorna True no máximo uma vez a cada `interval` segundos por bloqueio."""
        if self.elevated or not supported():
            return False
        try:
            blocked = self.probe(point) is True
        except Exception:  # Diagnóstico opcional: nunca impede a entrada.
            return False
        now = self.clock()
        if not blocked or (self.last is not None and now - self.last < self.interval):
            return False
        self.last = now
        return True


# --- Tarefa agendada ---------------------------------------------------------------

def current_sid():
    output = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                            check=True, creationflags=CREATE_NO_WINDOW).stdout
    sid = output.strip().rsplit(",", 1)[-1].strip().strip('"')
    if not sid.startswith("S-1-"):
        raise OSError("Não foi possível identificar o usuário do Windows.")
    return sid


def launch_command():
    """Executável e argumentos que a tarefa usa."""
    if getattr(sys, "frozen", False):
        from .installation import canonical_path
        target = canonical_path()
        exe = target if target.exists() else Path(sys.executable)
        return str(exe), ["--autostart"]
    exe = Path(sys.executable)
    exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    return str(exe), ["-m", "markcontrol", "--autostart"]


def task_xml(sid, command, arguments, workdir):
    args = subprocess.list2cmdline(arguments)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Inicia o Mark Mouse e Teclado Control com permissão para controlar janelas de administrador.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(sid)}</UserId>
      <Delay>PT5S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(sid)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args):
    result = subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip() or "O Agendador de Tarefas recusou a operação.")


def register_task():
    command, arguments = launch_command()
    xml = task_xml(current_sid(), command, arguments, str(Path(command).parent))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")


def delete_task():
    with suppress(OSError):
        _schtasks("/Delete", "/TN", TASK_NAME, "/F")


def task_exists():
    result = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True,
                            creationflags=CREATE_NO_WINDOW)
    return result.returncode == 0


def required():
    """O executável publicado no Windows sempre roda como administrador."""
    return supported() and bool(getattr(sys, "frozen", False))


def relaunch(argv):
    """Reabre o executável atual elevado (UAC). Retorna False se o usuário cancelar."""
    import ctypes
    from ctypes import wintypes
    args = [arg for arg in argv if arg != WAIT_PREVIOUS] + [WAIT_PREVIOUS]
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.LPCWSTR, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    code = shell.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(args),
                               str(Path(sys.executable).parent), 1) or 0
    if code > 32:
        return True
    if code in (5, 1223):  # Acesso negado / cancelado pelo usuário no UAC.
        return False
    raise OSError(f"O Windows não iniciou o aplicativo como administrador (código {code}).")


def wait_previous(folder, timeout=20):
    """A cópia não elevada encerra depois de pedir o UAC; espera ela soltar a instância."""
    from . import installation
    installation.wait_for(lambda: installation.notify(folder, "status") is None, timeout)
    installation.wait_for(lambda: not installation.product_port_busy(), 5)


def refuse_message():
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None, "O Mark Mouse e Teclado Control precisa ser aberto como administrador para controlar "
        "todas as janelas deste PC. Abra novamente e confirme o aviso do Windows.",
        "Mark Mouse e Teclado Control", 0x30)
