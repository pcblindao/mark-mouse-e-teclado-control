import json

import pytest

from markcontrol.security import Authorization, TTL, decode_message, private_ip


@pytest.mark.parametrize("ip", [
    "10.0.4.1", "172.16.0.2", "172.31.255.255", "192.168.0.1",
    "127.3.2.1", "::1", "fe80::12", "febf::a", "::ffff:192.168.1.1",
])
def test_private_ip(ip):
    assert private_ip(ip)


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "172.15.1.1", "172.32.1.1", "100.64.1.1", "169.254.1.1",
    "0.0.0.0", "192.0.2.1", "fc00::1", "fec0::1", "::ffff:1.1.1.1", "inválido",
])
def test_other_ip(ip):
    assert not private_ip(ip)


@pytest.fixture
def auth():
    now = [1000.0]
    verify = lambda body: {} if body == {"pin": "01234567"} else None
    return Authorization(verify, lambda: now[0]), now


def test_pin_and_token(auth):
    auth, _ = auth
    ip = "127.0.0.1"
    assert auth.login(ip, "１２３４５６７８") == (401, None)
    code, token = auth.login(ip, {"pin": "01234567"})
    assert code == 200
    token = token["token"]
    assert len(bytes.fromhex(token)) == 32
    assert auth.claim("127.0.0.2", token) is None
    assert auth.claim(ip, "!" * 64) is None
    assert auth.claim(ip, token).alive()
    assert auth.claim(ip, token) is None


def test_pending_expiration(auth):
    auth, now = auth
    _, token = auth.login("127.0.0.1", {"pin": "01234567"})
    now[0] += TTL
    assert auth.claim("127.0.0.1", token["token"]) is None


def test_active_expiration_and_renewal(auth):
    auth, now = auth
    ip = "127.0.0.1"
    _, token = auth.login(ip, {"pin": "01234567"})
    session = auth.claim(ip, token["token"])
    now[0] += TTL - 1
    session.renew()
    now[0] += TTL - 1
    assert session.alive()
    now[0] += 1
    session.renew()
    assert not session.alive()


def test_rate_limit(auth):
    auth, now = auth
    first, other = "127.0.0.1", "127.0.0.2"
    for _ in range(4):
        assert auth.login(first, "x") == (401, None)
    assert auth.login(first, "x") == (429, None)
    assert auth.login(other, {"pin": "01234567"})[0] == 200
    now[0] += 599
    assert auth.login(first, {"pin": "01234567"}) == (429, None)
    now[0] += 1
    assert auth.login(first, {"pin": "01234567"})[0] == 200


def test_old_failures_leave_window(auth):
    auth, now = auth
    ip = "127.0.0.1"
    for _ in range(4):
        auth.login(ip, "x")
    now[0] += 300
    assert auth.login(ip, "x") == (401, None)


def test_decode_good_and_bad():
    move = {"t": "move", "x": 0.5, "y": 1}
    assert decode_message(json.dumps(move)) == move
    assert decode_message('{"t":"move","x":true,"y":1}') is None
    assert decode_message('{"t":"move","x":0,"x":1,"y":1}') is None
    assert decode_message('{"t":"shell","s":"abc"}') is None
