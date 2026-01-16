import pytest

import src.broadcast_manager as bm
import src.proxy_broadcast as pb
from src.proxy_checker import ProxyCheckResult, parse_proxy_line


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in ("PROXY_PORTS", "PROXY_PORT_MIN", "PROXY_PORT_MAX"):
        monkeypatch.delenv(k, raising=False)


def test_candidate_ports_default():
    assert pb.candidate_ports() == list(range(10000, 10010))


def test_candidate_ports_explicit_list(monkeypatch):
    monkeypatch.setenv("PROXY_PORTS", "10001, 10004 ;10009")
    assert pb.candidate_ports() == [10001, 10004, 10009]


def test_candidate_ports_range(monkeypatch):
    monkeypatch.setenv("PROXY_PORT_MIN", "10002")
    monkeypatch.setenv("PROXY_PORT_MAX", "10005")
    assert pb.candidate_ports() == [10002, 10003, 10004, 10005]


def _fake_verify(latency_by_port, dead=()):
    def _v(proxy, level="L2", timeout=15.0):
        port = int(proxy["port"])
        if port in dead:
            return ProxyCheckResult(ok=False, latency_ms=float("inf"))
        return ProxyCheckResult(ok=True, latency_ms=latency_by_port.get(port, 100.0))
    return _v


def test_check_ports_sorted(monkeypatch):
    monkeypatch.setattr(pb, "verify_proxy",
                        _fake_verify({10000: 300, 10001: 100, 10002: 200}, dead=(10007,)))
    res = pb.check_ports("h", "u", "p", ports=[10000, 10001, 10002, 10007])
    assert res[0]["port"] == 10001 and res[0]["ok"] is True
    assert res[-1]["port"] == 10007 and res[-1]["ok"] is False


def test_pick_fastest_port(monkeypatch):
    monkeypatch.setattr(pb, "verify_proxy", _fake_verify({10000: 50, 10001: 10, 10002: 30}))
    assert pb.pick_fastest_port("h", "u", "p", ports=[10000, 10001, 10002]) == 10001


def test_pick_fastest_port_all_dead(monkeypatch):
    monkeypatch.setattr(pb, "verify_proxy", _fake_verify({}, dead=(10000, 10001)))
    assert pb.pick_fastest_port("h", "u", "p", ports=[10000, 10001]) is None


def test_next_available_port_excludes_current(monkeypatch):
    # the current port is the fastest, rotation still has to move away from it
    monkeypatch.setattr(pb, "verify_proxy", _fake_verify({10000: 10, 10001: 20, 10002: 30}))
    assert pb.next_available_port(10000, "h", "u", "p", ports=[10000, 10001, 10002]) == 10001


def test_next_port_only_current_left(monkeypatch):
    monkeypatch.setattr(pb, "verify_proxy", _fake_verify({10000: 10}, dead=(10001, 10002)))
    assert pb.next_available_port(10000, "h", "u", "p", ports=[10000, 10001, 10002]) == 10000


def test_next_port_all_dead(monkeypatch):
    monkeypatch.setattr(pb, "verify_proxy", _fake_verify({}, dead=(10000, 10001)))
    assert pb.next_available_port(10000, "h", "u", "p", ports=[10000, 10001]) is None


class _FakeADB:
    def __init__(self, text):
        self._text = text

    def get_screen_text(self):
        return self._text


def test_is_device_connecting_true():
    assert pb.is_device_connecting(_FakeADB('<node text="Connecting..." /><node text="Chats"/>')) is True


def test_is_device_connecting_false():
    assert pb.is_device_connecting(_FakeADB('<node text="Chats"/>')) is False


def test_is_device_connecting_given_text():
    assert pb.is_device_connecting(None, screen_text="Connecting…") is True


def test_parse_proxy_line_formats():
    assert parse_proxy_line("proxy.example.com:10000:user:pw")["username"] == "user"
    named = parse_proxy_line("Home proxy.example.com:10000:user:pw")
    assert named["name"] == "Home" and named["port"] == "10000"
    url = parse_proxy_line("socks5://user:pw@proxy.example.com:10001")
    assert (url["host"], url["port"], url["password"]) == ("proxy.example.com", "10001", "pw")
    assert parse_proxy_line("proxy.example.com:10002")["username"] == ""
    assert parse_proxy_line("# comment") is None
    assert parse_proxy_line("garbage") is None


def test_current_port_reads_file(tmp_path, monkeypatch):
    pf = tmp_path / "broadcast_port.txt"
    pf.write_text("10005", encoding="utf-8")
    monkeypatch.setattr(bm, "PORT_FILE", pf)
    assert bm.BroadcastManager().current_port() == 10005


def test_current_port_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "PORT_FILE", tmp_path / "nope.txt")
    assert bm.BroadcastManager().current_port() is None


def test_rotate_writes_next_port(tmp_path, monkeypatch):
    pf = tmp_path / "broadcast_port.txt"
    pf.write_text("10000", encoding="utf-8")
    monkeypatch.setattr(bm, "PORT_FILE", pf)
    monkeypatch.setenv("PROXY_HOST", "h")
    monkeypatch.setenv("PROXY_USER", "u")
    monkeypatch.setenv("PROXY_PASS", "p")
    monkeypatch.setattr(pb, "next_available_port", lambda cur, *a, **k: 10003)
    mgr = bm.BroadcastManager()
    assert mgr.rotate() == 10003
    assert pf.read_text(encoding="utf-8").strip() == "10003"


def test_rotate_no_ports_available(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "PORT_FILE", tmp_path / "p.txt")
    monkeypatch.setenv("PROXY_HOST", "h")
    monkeypatch.setattr(pb, "next_available_port", lambda *a, **k: None)
    assert bm.BroadcastManager().rotate() is None


def test_port_listening_false():
    assert bm.BroadcastManager._port_listening(59987) is False
