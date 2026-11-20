import asyncio
import json
import os
import re
import socket
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from queue import SimpleQueue
from dataclasses import replace
from types import SimpleNamespace

import pytest

from markcontrol.config import Config, pin_hash
from markcontrol.discovery import Discovery, PORT, REQUEST, parse
from PIL import Image
from markcontrol import cli, client, discovery
from markcontrol.gui import app, autostart, viewer
from markcontrol.gui.viewer import Viewer, protocol_key
from markcontrol.security import Authorization
from markcontrol.server import Agent
from markcontrol.trust import Changed, Trust


class Request(dict):
    content_type = "application/json"

    def __init__(self, body):
        super().__init__(peer="192.168.0.8")
        self.body = json.dumps(body)

    async def json(self, loads):
        return loads(self.body)


def test_login_and_pairing(tmp_path):
    cfg = Config(tmp_path)
    agent = SimpleNamespace(control=None, auth=Authorization(cfg.authenticate))

    def login(body, status):
        response = asyncio.run(Agent.login(agent, Request(body)))
        assert response.status == status
        return json.loads(response.body)

    token = login({}, 200)["token"]
    assert agent.auth.claim("192.168.0.9", token) is None
    assert agent.auth.claim("192.168.0.8", token).alive()
    login(None, 401)
    login({"extra": True}, 401)
    cfg.save("Oficina", False, 75, "12345678")
    saved = Config(tmp_path).read()
    assert (saved["name"], saved["clipboard"], saved["quality"]) == ("Oficina", False, 75)
    assert saved["pin"] == pin_hash("12345678", saved["pin"].split(":")[0])
    assert pin_hash("12345678") != saved["pin"]
    login({}, 401)
    login({"pin": "１２３４５６７８"}, 401)
    key = login({"pin": "12345678", "remember": True}, 200)["device_key"]
    assert len(bytes.fromhex(key)) == 32
    assert key not in cfg.path.read_text()
    login({"device_key": key}, 200)
    cfg.forget()
    login({"device_key": key}, 429)
    agent.auth = Authorization(Config(tmp_path).authenticate)
    login({"device_key": key}, 401)
    agent.control = object()
    login({"pin": "12345678"}, 409)
    if os.name == "posix":
        assert cfg.path.stat().st_mode & 0o777 == 0o600


FP = ":".join(["ab"]*32)
ADDR = ("192.168.1.2", PORT)
AD = dict(name="Estúdio", fingerprint=FP, port=47801, busy=False, pin_required=True)


def packet(**changes):
    return json.dumps({**AD, **changes}).encode()


def test_tofu(tmp_path):
    peer = parse(packet(), ADDR)
    trust = Trust(tmp_path)
    assert trust.key(peer) is None and not trust.path.exists()
    trust.verified(peer, "cd"*32)
    assert Trust(tmp_path).key(peer) == "cd"*32
    changed = replace(peer, fingerprint=FP.replace("ab", "cd"))
    for other in (changed, replace(changed, name="Outro")):
        with pytest.raises(Changed):
            trust.key(other)
    assert trust.key(changed, replace=True) is None
    trust.verified(changed, replace=True)
    assert trust.key(changed) is None
    if os.name == "posix":
        assert trust.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("raw,addr", [
    (packet(name="x\n"), ADDR), (packet(busy=1), ADDR), (packet(pin_required="false"), ADDR),
    (packet(port=True), ADDR), (packet(fingerprint="0"*64), ADDR), (packet(extra={}), ADDR),
    (packet(), ("8.8.8.8", PORT)), (packet(), (ADDR[0], 1)), (b"["*1500, ADDR),
    (packet()[:-1] + b', "busy": true}', ADDR), (b"\xff", ADDR),
])
def test_bad_discovery(raw, addr):
    assert parse(raw, addr) is None


def test_discovery_reply():
    sent = []
    protocol = Discovery(lambda: AD)
    protocol.connection_made(SimpleNamespace(sendto=lambda data, addr: sent.append((data, addr))))
    protocol.datagram_received(REQUEST, ("1.1.1.1", PORT))
    protocol.datagram_received(b"junk", ADDR)
    assert not sent
    protocol.datagram_received(REQUEST, ADDR)
    (data, addr), = sent
    assert len(data) <= len(REQUEST)
    peer = parse(data, addr)
    assert peer.ip == ADDR[0] and peer.pin_required and not peer.busy


@pytest.mark.parametrize("sym,char,key", [
    ("Control_R", "", "Control"), ("Prior", "", "PageUp"), ("space", " ", " "),
    ("A", "", "a"), ("exclam", "!", "!"), ("F12", "", "F12"), ("F13", "", None),
])
def test_tk_keys(sym, char, key):
    assert protocol_key(sym, char) == key


def test_same_name_dhcp_and_retrust(tmp_path):
    a = parse(packet(), ADDR)
    b = replace(a, ip="192.168.1.8", fingerprint=FP.replace("ab", "cd"))
    trust = Trust(tmp_path)
    # Formato anterior, por IP.
    trust.path.write_text(json.dumps({a.ip: dict(name=a.name, fingerprint=FP, key="a"*64)}))
    assert trust.key(b) is None
    trust.verified(b, "b"*64)
    moved = replace(a, ip="192.168.1.9", name="Outro")
    assert trust.key(moved) == "a"*64
    trust.verified(moved)
    assert trust.key(replace(b, ip=a.ip)) == "b"*64
    replacement = replace(b, fingerprint=FP.replace("ab", "ef"))
    with pytest.raises(Changed):
        trust.key(replacement)
    assert trust.key(replacement, replace=True) is None
    trust.verified(replacement, replace=True)
    assert trust.key(moved) == "a"*64
    assert trust.key(replace(b, ip="192.168.1.10")) == "b"*64
    assert set(json.loads(trust.path.read_text())) == {FP, b.fingerprint, replacement.fingerprint}


@pytest.mark.parametrize("sym,char", [("ccedilla", "ç"), ("Ccedilla", "Ç"), ("a", "á"),
                                       ("a", "ã"), ("EuroSign", "€"), ("space", "~")])
def test_tk_composition(sym, char):
    messages = []
    ui = SimpleNamespace(photo=True, keys={}, buttons=set(), client=SimpleNamespace(send=messages.append))
    for symbol, value in (("dead_tilde", "~"), (sym, char)):
        event = SimpleNamespace(keysym=symbol, char=value, keycode=65, state=0)
        Viewer.key(ui, event, True)
        Viewer.key(ui, event, False)
    Viewer.release(ui)
    if char == "~":
        assert messages == [dict(t="key", k="~", d=d) for d in ("down", "up")]
    else:
        assert messages == [dict(t="text", s=char)]


def test_local_broadcasts():
    targets = discovery.broadcast_targets([("10.1.4.2", "255.255.0.0"), ("192.168.2.1", None),
                                           ("192.168.2.2", None), ("127.0.0.1", None),
                                           ("8.8.8.8", None), ("10.2.3.4", "255.255.255.255")])
    assert set(targets) == {"255.255.255.255", "10.1.255.255", "192.168.2.255"}
    assert len(targets) == 3


def test_config_cache_and_recovery(tmp_path, monkeypatch):
    cfg = Config(tmp_path)
    cfg.save("PC", False, 65, "12345678")
    warnings = []
    good = cfg.refresh(warnings.append, opening=True)
    with monkeypatch.context() as ctx:
        ctx.setattr(cfg, "read", lambda: pytest.fail("releitura sem alteração"))
        assert cfg.refresh(warnings.append) == good
    damaged = b'{"pin":'
    cfg.path.write_bytes(damaged)
    for _ in range(2):
        assert cfg.refresh(warnings.append) == good
    assert len(warnings) == 1 and cfg.authenticate({}) is None
    cfg.path.write_text(json.dumps(dict(good, quality=77)))
    assert cfg.refresh(warnings.append)["quality"] == 77
    cfg.path.write_bytes(damaged)
    reopened = Config(tmp_path)
    defaults = reopened.refresh(warnings.append, opening=True)
    assert defaults["quality"] == 65 and defaults["review_required"]
    assert (tmp_path / "config.json.bad").read_bytes() == damaged
    assert reopened.authenticate({}) is None
    reopened.save("PC", False, 65, "12345678")
    assert reopened.authenticate({"pin": "12345678"}) == {}


def test_desktop_exec(tmp_path, monkeypatch):
    path = tmp_path / "markcontrol.desktop"
    exe = '/opt/a b/$' + chr(96) + '"%\\x\ttool'
    monkeypatch.setattr(autostart, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(autostart, "desktop_file", lambda: path)
    monkeypatch.setattr(autostart, "command", lambda: [exe, "--autostart"])
    autostart.set_enabled(True)
    special = r'\\$' + r'\\' + chr(96) + r'\\"%%\\\\x\ttool'
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "Name=Mark Mouse e Teclado Control" in lines
    assert 'Exec="/opt/a b/' + special + '" "--autostart"' in lines


def test_client_frame_and_tk_draw(monkeypatch):
    data = BytesIO()
    Image.new("RGB", (200, 100)).save(data, "JPEG")
    frame = client.prepare_frame(data.getvalue(), (90, 60))
    assert frame.size == (90, 45)
    ui = SimpleNamespace(canvas=SimpleNamespace(itemconfigure=lambda *a, **kw: None), item=1)
    monkeypatch.setattr(viewer.ImageTk, "PhotoImage", lambda image, master: image)
    monkeypatch.setattr(Image.Image, "resize", lambda *a, **kw: pytest.fail("resize no Tk"))
    Viewer.draw(ui, frame)
    assert ui.photo is frame


def test_release_identity_and_firewall():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    heading = re.search(
        r"(?ims)<h1(?:\s[^>]*)?>(.*?)</h1\s*>|^# ([^\n]*)",
        readme,
    )
    assert heading is not None
    assert (heading.group(1) or heading.group(2)) == "Mark Mouse e Teclado Control"
    assert re.search(
        r"\bMarque (?:somente )?Redes privadas e deixe Redes públicas desmarcada\b",
        readme.replace("**", "").replace("__", ""),
    )
    assert REQUEST == b"MARKCONTROL?".ljust(1280, b" ")
    assert (root / "markcontrol.spec").is_file()


def test_unicast_targets():
    addresses = {
        "192.168.100.103": "255.255.255.0",
        "192.168.100.2": "255.255.255.252",
        "192.168.100.4": None,
        "172.16.0.2": "255.255.255.252",
        "10.10.0.2": "255.255.255.254",
        "10.11.0.2": "255.255.248.0",
        "10.12.0.2": None,
        "8.8.8.8": "255.255.255.0",
        "127.0.0.1": "255.255.255.0",
    }
    targets = discovery.unicast_targets(addresses)
    assert set(targets) == {"172.16.0.1", *(
        f"192.168.100.{i}" for i in range(1, 255) if i not in (2, 3, 4, 103)
    )}
    assert len(targets) == len(set(targets))


def test_unicast_cap_prefers_main_network():
    import ipaddress

    primary = ipaddress.IPv4Network("192.168.100.0/22")
    targets = discovery.unicast_targets({
        "10.0.0.1": "255.255.252.0",
        "192.168.100.103": "255.255.252.0",
    }, primary)
    assert len(targets) == len(set(targets)) == 1024
    assert len([ip for ip in targets if ipaddress.IPv4Address(ip) in primary]) == 1021
    assert targets[-3:] == ["10.0.0.2", "10.0.0.3", "10.0.0.4"]


def test_scan_uses_padded_requests_and_groups_responses(monkeypatch, capsys):
    events = []
    options = []
    incoming = []
    reset = True
    alternate = "172.16.0.1"
    main = "192.168.100.1"
    other = FP.replace("ab", "cd")

    def send(data, target):
        assert data == REQUEST and target[1] == PORT
        events.append(target[0])
        if target[0] == main:
            incoming.extend([(packet(), (main, PORT)),
                             (packet(fingerprint=other), ("192.168.100.9", PORT))])
        if target[0] == "192.168.100.3":
            incoming.extend([(packet(busy=True), (alternate, PORT)),
                             (packet(busy=True), (alternate, PORT))])

    def recv(_):
        nonlocal reset
        if incoming:
            return incoming.pop(0)
        if events[-1] == main and reset:
            reset = False
            incoming.append((packet(), ("8.8.8.8", PORT)))
            raise ConnectionResetError()
        raise socket.timeout

    class FakeSocket:
        setsockopt = staticmethod(lambda *opt: options.append(opt))
        setblocking = staticmethod(lambda _: None)
        settimeout = staticmethod(lambda _: None)
        sendto = staticmethod(send)
        recvfrom = staticmethod(recv)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(discovery, "local_addresses", lambda: {
        "192.168.100.2": "255.255.255.248",
        "172.16.0.2": "255.255.255.252",
    }, raising=False)
    monkeypatch.setattr(discovery, "route_ip", lambda: "192.168.100.2", raising=False)
    monkeypatch.setattr(discovery.socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(discovery, "local_broadcasts", lambda: ["255.255.255.255"], raising=False)
    monkeypatch.setattr(discovery, "unicast_delay",
                        lambda: nullcontext(lambda: events.append("pause")), raising=False)

    assert cli.main(["scan", "--timeout", "0.1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert f"https://{main}:47801/" in lines[0] and "em uso" in lines[0]
    assert f"(conexão: https://{alternate}:47801/)" in lines[0]
    assert other in lines[1] and "192.168.100.9" in lines[1]
    assert (socket.IPPROTO_IP, socket.IP_TTL, 1) in options
    assert events == ["172.16.0.3", "192.168.100.7", "255.255.255.255",
                      main, "pause", "192.168.100.3", "pause",
                      "192.168.100.4", "pause", "192.168.100.5", "pause",
                      "192.168.100.6", "pause", alternate, "pause"]


def test_gui_prefers_main_ip_but_connects_to_latest_reply(monkeypatch):
    import ipaddress

    main = parse(packet(), ADDR)
    alternate = replace(main, ip="172.16.0.1")
    rows = {}
    tree = SimpleNamespace(
        insert=lambda *a, iid, values: rows.update({iid: values}),
        item=lambda iid, values: rows.update({iid: values}),
        delete=rows.pop, selection=lambda: (main.fingerprint,),
    )
    hints = []
    ui = SimpleNamespace(
        root=None, tree=tree, peers={}, replies={}, empty_since=0, fingerprint="",
        found=SimpleQueue(), primary=None, hint=SimpleNamespace(set=hints.append),
        config=SimpleNamespace(refresh=lambda *_: {}),
        warning=SimpleNamespace(set=lambda _: None),
    )
    now = [10.0]
    monkeypatch.setattr(app.time, "perf_counter", lambda: now[0])
    app.App.update_list(ui)
    assert hints[-1] == ""
    now[0] = 10.1
    app.App.update_list(ui)
    assert hints[-1] == (
        "Nenhum PC encontrado. No outro PC, abra o Mark Mouse e Teclado Control e clique Ativar."
    )

    net = ipaddress.IPv4Network("192.168.1.0/24")
    ui.found.put((net, {main.ip: (10.1, main)}))
    app.App.update_list(ui)
    now[0] = 12.0
    ui.found.put((net, {alternate.ip: (12.0, alternate)}))
    app.App.update_list(ui)
    assert rows == {main.fingerprint: (main.name, main.ip, "Livre")}
    assert hints[-1] == ""

    connected = []
    monkeypatch.setattr(app, "Viewer", lambda root, peer, cfg: connected.append(peer))
    app.App.connect(ui)
    assert connected == [alternate]

    now[0] = 20.2
    app.App.update_list(ui)
    assert rows == {main.fingerprint: (main.name, alternate.ip, "Livre")}
    now[0] = 22.1
    app.App.update_list(ui)
    assert rows == {} and hints[-1] == ""


def test_public_network_settings(monkeypatch):
    from markcontrol.gui import network

    calls = []
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(network.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout="wifi 8.8.8.8\nwifi 127.0.0.1\nethernet 192.168.100.3\n")

    monkeypatch.setattr(network.subprocess, "run", run)
    monkeypatch.setattr(network, "sys", SimpleNamespace(platform="linux"))
    assert network.public_network() is None and not calls
    monkeypatch.setattr(network, "sys", SimpleNamespace(platform="win32"))
    assert network.public_network() == "ethernet"
    (command, options), = calls
    assert command[1:] == ["-NoProfile", "-NonInteractive", "-Command", network.PROFILE_COMMAND]
    assert options["timeout"] == 8 and options["creationflags"] != 0
    assert "Get-NetConnectionProfile -NetworkCategory Public" in command[-1]
    assert "Get-NetIPAddress" in command[-1]
    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="ethernet 192.168.100.3\nwifi 192.168.100.4\n"))
    assert network.public_network() == "wifi"

    text, button, opened = [], [], []
    ui = SimpleNamespace(
        tree=object(),
        network_text=SimpleNamespace(configure=lambda **kw: text.append(kw["text"])),
        network_button=SimpleNamespace(configure=lambda **kw: button.append(kw["command"])),
        network_panel=SimpleNamespace(pack=lambda **kw: None),
    )
    ui.open_network_settings = lambda kind: app.App.open_network_settings(ui, kind)
    monkeypatch.setattr(app.os, "startfile", opened.append, raising=False)
    app.App.show_public_network(ui, "wifi")
    assert text == [
        "O Wi-Fi está como rede Pública: o Windows bloqueia o Mark Mouse e Teclado Control. "
        "Abra Configurações > Rede > Wi-Fi > esta rede > Perfil: Privada."
    ]
    assert opened == []
    button[0]()
    assert opened == ["ms-settings:network-wifi"]
