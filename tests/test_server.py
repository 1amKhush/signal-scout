import http.client
import json
import ssl
import threading
import time

import pytest

from wifispot.phone import Phone
from wifispot.server import App, Server
from wifispot.survey import SurveyManager

from test_survey import FakeMonitor, wait_idle


class LiveMonitor(FakeMonitor):
    iface = "wlan-test"
    aps = []
    aps_at = None

    def connection(self):
        return {}

    def snapshot(self):
        return {"t": time.time(), "last60": {}}


@pytest.fixture
def app(tmp_path, monkeypatch):
    mon, phone = LiveMonitor(), Phone()
    surveys = SurveyManager(mon, phone, tmp_path, window_s=0.05)
    app = App(mon, surveys, phone, tmp_path, port=0, phone_port=0)
    srv = Server(("127.0.0.1", 0), app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app.port = srv.server_address[1]
    monkeypatch.setattr("wifispot.wifi.iface_ip", lambda iface: "127.0.0.1")
    yield app
    app.stop_phone()
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, host=None, ctype="application/json", tls=False):
    cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
    kw = {"context": ssl._create_unverified_context()} if tls else {}
    c = cls("127.0.0.1", port, timeout=10, **kw)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = ctype
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    out = r.status, json.loads(r.read() or b"null") if r.getheader("Content-Type") == "application/json" else None
    c.close()
    return out


def test_local_guards(app):
    assert call(app.port, "GET", "/api/survey")[0] == 200
    assert call(app.port, "GET", "/api/survey", host="attacker.example:80")[0] == 403
    assert call(app.port, "POST", "/api/survey/new", {}, ctype="text/plain")[0] == 415
    assert call(app.port, "GET", "/static/../server.py")[0] == 404


def test_phone_pairing_is_token_gated(app):
    status, pair = call(app.port, "POST", "/api/phone/pair", {})
    assert status == 200 and pair["url"].startswith("https://127.0.0.1:") and "<svg" in pair["qr_svg"]
    port = app.phone_server.server_address[1]
    token = pair["url"].rsplit("/", 1)[1]
    assert call(port, "GET", f"/p/{token}", tls=True)[0] == 200
    assert call(port, "GET", "/p/wrong", tls=True)[0] == 404
    assert call(port, "GET", "/api/survey", tls=True)[0] == 404        # laptop API is not exposed
    assert call(port, "POST", "/api/p/wrong/report", {}, tls=True)[0] == 403
    status, st = call(port, "POST", f"/api/p/{token}/report",
                      {"fix": {"lat": 26, "lon": 73, "acc": 7, "ts": 1, "age_ms": 0}}, tls=True)
    assert status == 200 and st["spots"] == 0
    assert app.phone.status(time.time())["acc"] == 7
    call(app.port, "POST", "/api/phone/unpair", {})
    assert not app.phone.check(token)


def test_settled_comparison_waits_for_data_since_arrival(app):
    call(app.port, "POST", "/api/survey/measure", {"label": "desk"})
    wait_idle(app.surveys)
    spot_id = app.surveys.state(time.time())["spots"][0]["id"]
    call(app.port, "POST", "/api/survey/choose", {"spot_id": spot_id})
    settled = call(app.port, "GET", "/api/live")[1]["settled"]
    assert settled["pending"] and "consistent" not in settled

    app.surveys.survey["chosen_at"] -= 30   # pretend the user arrived 30 s ago
    settled = call(app.port, "GET", "/api/live")[1]["settled"]
    assert not settled["pending"] and settled["consistent"] and settled["differences"] == []
