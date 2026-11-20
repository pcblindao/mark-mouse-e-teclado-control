import os
import sys

from markcontrol.cli import main


if __name__ == "__main__":
    if os.name == "nt" and sys.stdout is None and sys.argv[1:] not in ([], ["--autostart"]):
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
    raise SystemExit(main())
