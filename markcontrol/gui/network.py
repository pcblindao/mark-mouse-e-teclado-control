import os
import subprocess
import sys
from pathlib import Path

from ..security import private_ip

SETTINGS = {
    "wifi": "ms-settings:network-wifi",
    "ethernet": "ms-settings:network-ethernet",
}
PROFILE_COMMAND = """
$wifi = @(Get-NetAdapter | Where-Object {
    $_.InterfaceType -eq 71 -or $_.NdisPhysicalMedium -in 1,9
} | Select-Object -ExpandProperty ifIndex)
foreach ($profile in Get-NetConnectionProfile -NetworkCategory Public) {
    $index = $profile.InterfaceIndex
    $kind = if ($index -in $wifi) { 'wifi' } else { 'ethernet' }
    Get-NetIPAddress -InterfaceIndex $index -AddressFamily IPv4 -AddressState Preferred |
        ForEach-Object { "$kind $($_.IPAddress)" }
}
"""


def public_network():
    if sys.platform != "win32":
        return None
    system = os.environ.get("SystemRoot")
    if not system:
        return None
    powershell = Path(system) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", PROFILE_COMMAND],
            capture_output=True, text=True, errors="replace", timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    kinds = set()
    for line in result.stdout.splitlines():
        kind, _, ip = line.strip().partition(" ")
        if kind in SETTINGS and ":" not in ip and private_ip(ip) and not ip.startswith("127."):
            kinds.add(kind)
    return next((kind for kind in SETTINGS if kind in kinds), None)
