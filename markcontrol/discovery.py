import asyncio
import ipaddress
import json
import re
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass

from .config import valid_name
from .security import private_ip

PORT = 47800
QUERY = b"MARKCONTROL?"
REQUEST = QUERY.ljust(1280, b" ")
FP = re.compile(r"(?:[0-9a-f]{2}:){31}[0-9a-f]{2}")
MAX_UNICAST = 1024
MAX_PEERS = 512


@dataclass(frozen=True)
class Peer:
    ip: str
    name: str
    port: int
    fingerprint: str
    busy: bool
    pin_required: bool


def parse(data, address):
    ip, port = address
    if port != PORT or not private_ip(ip) or len(data) > len(REQUEST):
        return None
    try:
        pairs = json.loads(data.decode("utf-8"), object_pairs_hook=list)
        if not isinstance(pairs, list) or not data.lstrip().startswith(b"{") or len(pairs) != 5:
            return None
        item = dict(pairs)
        if item.keys() != {"name", "port", "fingerprint", "busy", "pin_required"}:
            return None
        if (not valid_name(item["name"]) or type(item["port"]) is not int
                or not 1 <= item["port"] <= 65535 or not isinstance(item["fingerprint"], str)
                or not FP.fullmatch(item["fingerprint"])
                or any(type(item[k]) is not bool for k in ("busy", "pin_required"))):
            return None
        return Peer(ip, **item)
    except (ValueError, TypeError, RecursionError):
        return None


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, describe):
        self.describe = describe

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        if not private_ip(address[0]):
            return
        if data == QUERY:
            self.transport.sendto(b"PAD 1280", address)
        elif data == REQUEST:
            reply = json.dumps(self.describe(), ensure_ascii=False, separators=(",", ":")).encode()
            if len(reply) <= len(data):
                self.transport.sendto(reply, address)


def interfaces():
    """Pares (IPv4, máscara) das interfaces locais que o sistema enumera."""
    import ctypes
    import struct

    if sys.platform == "win32":
        from ctypes import wintypes

        # GetIpAddrTable: DWORD de contagem seguido de linhas MIB_IPADDRROW.
        size = wintypes.ULONG()
        table = ctypes.windll.iphlpapi.GetIpAddrTable
        if table(None, ctypes.byref(size), False) == 122:
            buf = ctypes.create_string_buffer(size.value)
            if table(buf, ctypes.byref(size), False) == 0:
                count, = struct.unpack_from("<I", buf)
                for i in range(count):
                    addr, mask, flags = struct.unpack_from("<4s4x4s10xH", buf, 4 + i * 24)
                    if not flags & 0x48:  # MIB_IPADDR_DISCONNECTED | MIB_IPADDR_DELETED
                        yield socket.inet_ntoa(addr), socket.inet_ntoa(mask)
    elif sys.platform.startswith("linux"):
        # getifaddrs inclui os IPv4 secundários que SIOCGIFADDR não enumera.
        class Sockaddr(ctypes.Structure):
            _fields_ = [("family", ctypes.c_ushort), ("data", ctypes.c_ubyte * 14)]

        class Ifaddrs(ctypes.Structure):
            pass

        Ifaddrs._fields_ = [
            ("next", ctypes.POINTER(Ifaddrs)), ("name", ctypes.c_char_p),
            ("flags", ctypes.c_uint), ("addr", ctypes.POINTER(Sockaddr)),
            ("mask", ctypes.POINTER(Sockaddr)), ("ifu", ctypes.c_void_p),
            ("data", ctypes.c_void_p),
        ]
        libc = ctypes.CDLL(None, use_errno=True)
        head = ctypes.POINTER(Ifaddrs)()
        if libc.getifaddrs(ctypes.byref(head)) != 0:
            raise OSError(ctypes.get_errno(), "getifaddrs")
        try:
            entry = head
            while entry:
                addr = entry.contents
                entry = addr.next
                # IFF_UP | IFF_RUNNING
                if addr.flags & 0x41 == 0x41 and addr.addr and addr.mask:
                    if addr.addr.contents.family == socket.AF_INET:
                        yield (socket.inet_ntoa(bytes(addr.addr.contents.data[2:6])),
                               socket.inet_ntoa(bytes(addr.mask.contents.data[2:6])))
        finally:
            libc.freeifaddrs(head)


def broadcast_targets(addresses):
    targets = {"255.255.255.255"}
    for address, mask in addresses:
        if not private_ip(address) or address.startswith("127."):
            continue
        net = ipaddress.IPv4Network((address, mask or 24), strict=False)
        target = str(net.broadcast_address)
        if net.prefixlen < 31 and private_ip(target):
            targets.add(target)
    return sorted(targets)


def local_addresses():
    try:
        addresses = dict(interfaces())
    except OSError:
        addresses = {}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            addresses.setdefault(info[4][0], None)
    except OSError:
        pass
    return addresses


def route_ip():
    # Consulta a rota padrão sem enviar um datagrama. O primeiro IP é reservado para documentação.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in ("192.0.2.1", "255.255.255.255"):
            try:
                route.connect((target, PORT))
                return route.getsockname()[0]
            except OSError:
                continue
    return None


def private_lan(ip, mask):
    if not mask or not private_ip(ip) or ip.startswith("127."):
        return None
    try:
        net = ipaddress.IPv4Network((ip, mask), strict=False)
    except ValueError:
        return None
    if private_ip(str(net.network_address)) and private_ip(str(net.broadcast_address)):
        return net
    return None


def unicast_targets(addresses, primary=None):
    own = {ipaddress.IPv4Address(ip) for ip in addresses}
    networks = {net for ip, mask in addresses.items() if (net := private_lan(ip, mask))}
    for net in networks:
        if net.prefixlen < 31:
            own.update((net.network_address, net.broadcast_address))
    targets = []
    for net in sorted(networks, key=lambda net: (net != primary, net)):
        if not 22 <= net.prefixlen <= 30:
            continue
        for ip in net.hosts():
            if ip not in own:
                targets.append(str(ip))
                if len(targets) == MAX_UNICAST:
                    return targets
                own.add(ip)
    return targets


def grouped_peers(replies, primary):
    preferred, latest = {}, {}
    for stamp, peer in replies.values():
        key = peer.fingerprint
        rank = (primary is not None and ipaddress.IPv4Address(peer.ip) in primary, stamp)
        if key not in preferred or rank >= preferred[key][0]:
            preferred[key] = rank, peer
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = stamp, peer
    return {key: (preferred[key][1], peer) for key, (_, peer) in latest.items()}


@contextmanager
def unicast_delay():
    def sleep():
        time.sleep(0.001)

    if sys.platform != "win32" or sys.version_info >= (3, 11):
        yield sleep
        return

    # O time.sleep do Python 3.10 no Windows usa o timer de baixa resolução.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    create = kernel.CreateWaitableTimerExW
    create.restype = wintypes.HANDLE
    timer = create(None, None, 2, 0x100002)  # High resolution, SYNCHRONIZE | TIMER_MODIFY_STATE
    if not timer:
        yield sleep
        return

    handle = wintypes.HANDLE(timer)
    due = ctypes.c_longlong(-10_000)  # Um milissegundo relativo, em unidades de 100 ns.

    def wait():
        if not kernel.SetWaitableTimer(handle, ctypes.byref(due), 0, None, None, False):
            sleep()
        elif kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
            sleep()

    try:
        yield wait
    finally:
        kernel.CloseHandle(handle)


def receive(sock, replies, limit):
    for _ in range(limit):
        try:
            data, address = sock.recvfrom(65535)
        except (BlockingIOError, socket.timeout):
            return False
        except (ConnectionResetError, ConnectionRefusedError):
            # ICMP de um host sem agente, comum no Windows durante a varredura.
            continue
        peer = parse(data, address)
        if peer and (peer.ip in replies or len(replies) < MAX_PEERS):
            replies[peer.ip] = time.perf_counter(), peer
    return True


def scan_cycle(timeout, broadcast):
    addresses = local_addresses()
    ip = route_ip()
    if ip is not None:
        addresses.setdefault(ip, None)
    primary = private_lan(ip, addresses.get(ip)) if ip is not None else None
    broadcasts = broadcast_targets(addresses.items()) if broadcast == "255.255.255.255" else [broadcast]
    replies = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
        sock.setblocking(False)
        sent = False

        def send(target):
            nonlocal sent, error
            try:
                sock.sendto(REQUEST, (target, PORT))
                sent = True
            except OSError as exc:
                error = exc

        error = None
        for target in broadcasts:
            send(target)
        with unicast_delay() as pause:
            for target in unicast_targets(addresses, primary):
                send(target)
                receive(sock, replies, 32)
                pause()
        if not sent:
            raise error
        deadline = time.perf_counter() + timeout
        while (remaining := deadline - time.perf_counter()) > 0:
            sock.settimeout(remaining)
            if not receive(sock, replies, 1):
                break
    return primary, replies


def scan(timeout, broadcast, *, with_routes=False):
    primary, replies = scan_cycle(timeout, broadcast)
    for preferred, latest in grouped_peers(replies, primary).values():
        yield (preferred, latest) if with_routes else preferred
