import random

from signalscout import analysis as a


def window(signal, rtt, loss=0.0, bitrate=300.0, n_sig=12, n_ping=48, seed=1, noise=1.0):
    rng = random.Random(seed)
    signals = [signal + rng.gauss(0, noise) for _ in range(n_sig)]
    rtts = [None if rng.random() < loss else max(0.5, rtt + rng.gauss(0, rtt * 0.2)) for _ in range(n_ping)]
    return signals, rtts, [bitrate] * n_sig


def spot(sid, t_end, *args, moved=False, **kw):
    s, r, b = window(*args, **kw)
    m = a.link_metrics(s, r, b)
    return {"id": sid, "label": sid, "t_end": t_end, "metrics": m, "score": a.score(m),
            "boot": a.bootstrap_scores(s, r, b, n=200, seed=hash(sid) & 0xFF), "moved": moved,
            "latency_target": "gateway"}


def test_link_metrics():
    m = a.link_metrics([-50, -52, -54], [2.0, None, 4.0, 3.0], [100, 200])
    assert m["signal_dbm"] == -52 and m["loss"] == 0.25 and m["rtt_ms"] == 3.0
    assert m["bitrate_mbps"] == 150 and m["ping_n"] == 4


def test_score_orders_obvious_cases():
    good = a.score(a.link_metrics(*window(-50, 3)))
    bad = a.score(a.link_metrics(*window(-78, 40, loss=0.05, bitrate=24)))
    assert 0 <= bad < 30 < 80 < good <= 100
    assert a.score(a.link_metrics([], [], [])) is None


def test_clearly_better_spot_is_recommended_with_high_confidence():
    spots = [spot("start", 1000, -72, 20, loss=0.03, bitrate=60),
             spot("window", 1000, -52, 3, bitrate=400),
             spot("hall", 1000, -66, 10, bitrate=120)]
    r = a.recommend(spots, now=1060)
    assert r["decision"] == "move" and r["label"] == "window" and r["confidence"] == "high"
    assert r["reference_label"] == "start"
    assert any(x["better"] and x["text"].startswith("signal") for x in r["reasons"])


def test_tiny_differences_mean_stay():
    spots = [spot("start", 1000, -60, 5, seed=1), spot("b", 1000, -60, 5, seed=2), spot("c", 1000, -59, 5, seed=3)]
    r = a.recommend(spots, now=1060)
    assert r["decision"] == "stay" and r["spot_id"] == "start"


def test_tie_between_two_better_spots_is_mentioned():
    spots = [spot("start", 1000, -76, 30, loss=0.04, bitrate=50),
             spot("b", 1000, -52, 3, seed=2), spot("c", 1000, -52, 3, seed=3)]
    r = a.recommend(spots, now=1060)
    assert r["decision"] == "move" and r["confidence"] == "high"
    assert any("about as well" in c for c in r["caveats"])


def test_one_alternative_is_thin_evidence():
    r = a.recommend([spot("start", 1000, -55, 3), spot("b", 1000, -56, 3, seed=4)], now=1060)
    assert r["decision"] == "stay" and r["confidence"] == "medium"


def test_starting_spot_best_means_stay():
    spots = [spot("start", 1000, -50, 3), spot("b", 1000, -75, 30, loss=0.04)]
    r = a.recommend(spots, now=1060)
    assert r["decision"] == "stay" and r["spot_id"] == "start"


def test_stale_and_movement_lower_confidence():
    spots = [spot("start", 0, -75, 30, loss=0.04), spot("b", 0, -50, 3, moved=True)]
    r = a.recommend(spots, now=a.STALE_S + 10)
    assert r["confidence"] == "low"
    assert any("ago" in c for c in r["caveats"]) and any("movement" in c for c in r["caveats"])


def test_need_two_spots():
    assert a.recommend([spot("start", 0, -60, 5)], now=1)["status"] == "need_more"


def test_stronger_ap_hint_same_band_only():
    aps = [{"bssid": "A", "ssid": "net", "band": "5", "channel": 36, "signal_pct": 50, "signal_dbm": -70},
           {"bssid": "B", "ssid": "net", "band": "2.4", "channel": 6, "signal_pct": 90, "signal_dbm": -46},
           {"bssid": "C", "ssid": "net", "band": "5", "channel": 149, "signal_pct": 70, "signal_dbm": -58}]
    assert a.stronger_ap_hint(aps, "A", "net")["bssid"] == "C"
    assert a.stronger_ap_hint(aps[:2], "A", "net") is None


def test_fuse_fixes_does_not_shrink_accuracy():
    fixes = [{"lat": 26.0, "lon": 73.0, "acc": 8.0}, {"lat": 26.00001, "lon": 73.0, "acc": 8.0},
             {"lat": 26.00002, "lon": 73.0, "acc": 8.0}]
    f = a.fuse_fixes(fixes)
    assert f["acc"] == 8.0 and f["n"] == 3 and f["quality"] == "good"
    assert abs(f["lat"] - 26.00001) < 1e-9


def test_local_xy():
    x, y = a.local_xy(26.0001, 73.0001, 26.0, 73.0)
    assert abs(y - 11.06) < 0.05 and abs(x - 10.0) < 0.1


def geo_spot(sid, dx_m, dy_m, acc):
    lat = 26.0 + dy_m / 110_574
    lon = 73.0 + dx_m / (111_320 * 0.8988)   # cos(26°)
    return {"id": sid, "label": sid, "position": {"lat": lat, "lon": lon, "acc": acc}, "canvas": None}


def test_map_layout_gates_on_accuracy_vs_spacing():
    far = [geo_spot(str(i), 30 * (i % 2), 30 * (i // 2), 5) for i in range(4)]
    m = a.map_layout(far)
    assert m["geo"]["resolvable"] and m["geo"]["interpolation"]

    near = [geo_spot(str(i), 4 * (i % 2), 4 * (i // 2), 15) for i in range(4)]
    m = a.map_layout(near)
    assert not m["geo"]["resolvable"] and not m["geo"]["interpolation"] and m["geo"]["note"]

    coarse = [geo_spot("a", 0, 0, 40), geo_spot("b", 50, 0, 40)]
    m = a.map_layout(coarse)
    assert m["geo"] is None and len(m["unplaced_geo"]) == 2


def test_canvas_interpolation_needs_four_placed_spots():
    spots = [{"id": str(i), "label": str(i), "position": None,
              "canvas": {"x": 0.2 * i, "y": 0.5} if i < 3 else None} for i in range(5)]
    assert a.map_layout(spots)["canvas"]["interpolation"] is False
    spots[3]["canvas"] = {"x": 0.9, "y": 0.9}
    assert a.map_layout(spots)["canvas"]["interpolation"] is True


def test_channel_relations():
    aps = [{"bssid": "me", "band": "2.4", "channel": 6, "span": [2427, 2447]},
           {"bssid": "co", "band": "2.4", "channel": 6, "span": [2427, 2447]},
           {"bssid": "ov", "band": "2.4", "channel": 8, "span": [2437, 2457]},
           {"bssid": "clear", "band": "2.4", "channel": 11, "span": [2452, 2472]},
           {"bssid": "5g", "band": "5", "channel": 36, "span": [5170, 5190]}]
    assert a.channel_relations(aps, "me") == {"co": "same", "ov": "overlap"}
