import time

from signalscout.phone import Phone
from signalscout.survey import SurveyManager


class FakeMonitor:
    def __init__(self):
        self.signal = -60

    def rescan(self, wait=0):
        return [{"bssid": "AA", "ssid": "net", "channel": 36, "band": "5", "width": 20,
                 "signal_pct": 70, "signal_dbm": -58, "in_use": True}]

    def window(self, t0, t1):
        return {"signals": [self.signal] * 10, "bitrates": [200.0] * 10,
                "rtts": [3.0] * 40, "latency_target": "gateway", "bssid": "AA", "roamed": False}


def wait_idle(sm, timeout=5):
    end = time.time() + timeout
    while sm.job and time.time() < end:
        time.sleep(0.01)
    assert not sm.job


def test_measure_place_choose_and_reload(tmp_path):
    mon, phone = FakeMonitor(), Phone()
    sm = SurveyManager(mon, phone, tmp_path, window_s=0.05)

    sm.measure("")
    wait_idle(sm)
    mon.signal = -50
    phone.report({"fix": {"lat": 26.0, "lon": 73.0, "acc": 6, "ts": 1, "age_ms": 0}}, time.time())
    sm.measure("window seat")
    wait_idle(sm)

    st = sm.state(time.time())
    assert [s["label"] for s in st["spots"]] == ["Start", "window seat"]
    assert st["spots"][0]["position"] is None          # no phone fix yet
    assert st["spots"][1]["position"]["acc"] == 6
    assert st["recommendation"]["status"] == "ok"

    second = st["spots"][1]["id"]
    sm.update_spot(second, {"canvas": {"x": 0.3, "y": 0.4}, "label": "Window"})
    sm.choose(second)

    reloaded = SurveyManager(mon, phone, tmp_path, window_s=0.05)
    s2 = reloaded.state(time.time())
    assert s2["chosen_id"] == second
    assert s2["spots"][1]["canvas"] == {"x": 0.3, "y": 0.4} and s2["spots"][1]["label"] == "Window"


def test_cancel_discards_window(tmp_path):
    sm = SurveyManager(FakeMonitor(), Phone(), tmp_path, window_s=5)
    sm.measure("x")
    sm.cancel()
    wait_idle(sm)
    assert sm.state(time.time())["spots"] == []


def test_phone_rejects_bad_fixes_and_dedupes():
    p = Phone()
    p.report({"fix": {"lat": 999, "lon": 0, "acc": 5, "ts": 1}}, 100)
    p.report({"fix": {"lat": "nan", "lon": 0, "acc": 5, "ts": 1}}, 100)
    p.report({"fix": {"lat": 1, "lon": 2, "acc": 5, "ts": 7, "age_ms": 1000}, "moving": True}, 100)
    p.report({"fix": {"lat": 1, "lon": 2, "acc": 5, "ts": 7, "age_ms": 2000}}, 101)
    assert len(p.fixes_between(0, 200)) == 1
    assert p.moved_between(99, 100.5) and not p.moved_between(100.5, 200)
    old = p.token
    p.rotate()
    assert p.token != old and not p.check(old) and p.fixes_between(0, 200) == []
