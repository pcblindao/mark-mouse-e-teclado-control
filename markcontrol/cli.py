import argparse
import ipaddress
import math
import os
import sys

from .discovery import scan
from .security import private_ip
from .diagnostics import event, setup


def address(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Informe um endereço IP literal.") from None
    if not (ip.is_unspecified or private_ip(str(ip))) or "%" in value:
        raise argparse.ArgumentTypeError("Use um IP privado, loopback ou endereço de escuta curinga.")
    return str(ip)


def cert_ip(value):
    ip = ipaddress.ip_address(address(value))
    if ip.is_unspecified:
        raise argparse.ArgumentTypeError("Informe um IP concreto para o certificado.")
    return ip


def broadcast_ip(value):
    if value == "255.255.255.255":
        return value
    ip = address(value)
    if ":" in ip or not private_ip(ip):
        raise argparse.ArgumentTypeError("Informe um broadcast IPv4 da sub-rede local.")
    return ip


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("A porta deve estar entre 1 e 65535.")
    return port


def timeout_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or not 0.1 <= seconds <= 30:
        raise argparse.ArgumentTypeError("O tempo deve estar entre 0,1 e 30 segundos.")
    return seconds


def name_text(value):
    if not 1 <= len(value) <= 255 or not value.isprintable() or not value.strip():
        raise argparse.ArgumentTypeError("Informe um nome visível de até 255 caracteres.")
    return value


def prepare_desktop():
    if sys.platform.startswith("linux"):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError(
                "Wayland detectado. Abra uma sessão X11 para usar o agente. "
                "XWayland isolado não oferece acesso à tela e às entradas nativas do Wayland."
            )
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("DISPLAY ausente. Execute no terminal da sessão gráfica X11.")
    elif sys.platform == "win32":
        import ctypes

        if ctypes.windll.shcore.SetProcessDpiAwareness(2) != 0:
            ctypes.windll.user32.SetProcessDPIAware()


def main(argv=None):
    setup()
    parser = argparse.ArgumentParser(prog="markcontrol")
    parser.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    agent = commands.add_parser("agent", help="Executa o agente visível.")
    agent.add_argument("--host", type=address, default="0.0.0.0")
    agent.add_argument("--port", type=port_number, default=47801)
    agent.add_argument("--name", type=name_text, default=None)
    agent.add_argument("--cert-ip", type=cert_ip, action="append", default=[])
    agent.add_argument("--no-clipboard", action="store_true")
    finder = commands.add_parser("scan", help="Descobre agentes na rede local.")
    finder.add_argument("--broadcast", type=broadcast_ip, default="255.255.255.255")
    finder.add_argument("--timeout", type=timeout_seconds, default=2.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            found = False
            for shown, latest in scan(args.timeout, args.broadcast, with_routes=True):
                found = True
                state = "em uso" if latest.busy else "livre"
                url = f"https://{shown.ip}:{shown.port}/"
                if (shown.ip, shown.port) != (latest.ip, latest.port):
                    url += f"  (conexão: https://{latest.ip}:{latest.port}/)"
                print(f"{latest.name}  {url}  {state}  SHA-256 {latest.fingerprint}")
            if not found:
                print("Nenhum agente encontrado.")
        else:
            prepare_desktop()
            from .gui.app import launch
            from . import installation

            headless = args.command == "agent"
            options = args if headless else parser.parse_args(["agent"])
            if installation.setup_running() and installation._instance is None:
                return 0
            startup = installation.acquire_desktop(background=headless or args.autostart)
            try:
                if startup.exit_code is not None:
                    return startup.exit_code
                return launch(options, headless, args.autostart)
            finally:
                startup.close()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        event("startup_failed", exc)
        print(f"Mark Mouse e Teclado Control: {exc}", file=sys.stderr)
        return 2
    return 0
