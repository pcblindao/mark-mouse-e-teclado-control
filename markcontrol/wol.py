"""Wake-on-LAN nos broadcasts dirigidos de interfaces privadas locais."""
import ipaddress
import re
import socket
import uuid

from .discovery import interfaces
from .security import NETWORKS


def mac_bytes(mac):
    if not isinstance(mac, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise ValueError("MAC inválido.")
    value = bytes.fromhex(mac.replace(":", ""))
    if value == bytes(6) or value[0] & 1:
        raise ValueError("MAC inválido.")
    return value


def local_mac():
    value = uuid.getnode().to_bytes(6, "big").hex(":")
    try:
        mac_bytes(value)
    except ValueError:
        return None
    return value


def broadcasts():
    result = {}
    for address, mask in interfaces():
        if not mask:
            continue
        net = ipaddress.IPv4Network((address, mask), strict=False)
        if net.prefixlen <= 30 and any(net.subnet_of(n) for n in NETWORKS
                                       if n.version == 4 and not n.is_loopback):
            result[str(net.broadcast_address)] = address
    return result


def wake(mac, broadcast):
    packet = b"\xff" * 6 + mac_bytes(mac) * 16
    source = broadcasts().get(broadcast)
    if source is None:
        raise ValueError("Escolha um broadcast de interface privada local.")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((source, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
        sock.sendto(packet, (broadcast, 9))
    print("Wake-on-LAN enviado.", flush=True)
