import os
import sys

from markcontrol.cli import main


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and sys.argv[1:] in (["--prepare-install"], ["--register-install"]):
        from markcontrol import installation
        try:
            result = installation.prepare_install() if sys.argv[1] == "--prepare-install" else installation.register_install()
        except Exception as exc:
            from markcontrol.diagnostics import event, setup
            setup()
            event("startup_failed", exc)
            result = 2
        raise SystemExit(result)
    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--apply-update"]:
        from pathlib import Path
        from markcontrol.updater import apply
        target, stage, version, pid = sys.argv[2:]
        try:
            result = apply(Path(target), Path(stage), version, int(pid))
        except Exception:
            result = 1
        raise SystemExit(result)
    if os.name == "nt" and sys.stdout is None and sys.argv[1:] not in ([], ["--autostart"]):
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
    if getattr(sys, "frozen", False) and os.name == "nt":
        # Sem privilégio de administrador o Windows (UIPI) descarta cliques e
        # teclas enviados para janelas elevadas. O app sempre roda elevado.
        from markcontrol import elevation
        wait_previous = elevation.WAIT_PREVIOUS in sys.argv[1:]
        if wait_previous:
            sys.argv.remove(elevation.WAIT_PREVIOUS)
        # A troca por atualização exige que este mesmo processo confirme a saúde;
        # a elevação acontece logo depois da confirmação (updater.restore).
        handshake = bool(os.environ.get("MARKCONTROL_UPDATE_STATE")
                         or os.environ.get("MARKCONTROL_INSTALL_HANDOFF"))
        if not handshake and not elevation.is_elevated():
            try:
                accepted = elevation.relaunch(sys.argv[1:])
            except OSError:
                accepted = False
            if not accepted and "--autostart" not in sys.argv[1:]:
                elevation.refuse_message()
            raise SystemExit(0 if accepted else 1)
        if wait_previous:
            from markcontrol.certs import data_dir
            elevation.wait_previous(data_dir())
    from markcontrol import installation
    after_install = sys.argv[1:2] == ["--after-install"]
    if after_install:
        del sys.argv[1]
    startup = None
    try:
        startup = installation.bootstrap(allow_setup=after_install)
        result = startup.exit_code if startup.exit_code is not None else main()
    except Exception as exc:
        from markcontrol.diagnostics import event, setup
        setup()
        event("startup_failed", exc)
        result = 2
    finally:
        if startup:
            startup.close()
    raise SystemExit(result)
