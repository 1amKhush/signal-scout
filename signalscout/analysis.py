"""Statistics, scoring, recommendation and position handling.

Pure functions over plain data so they can be tested without hardware.
"""

from __future__ import annotations

import math
import random
import statistics

FRESH_S = 5 * 60          # younger measurements are "fresh"
STALE_S = 15 * 60         # older ones are "stale" and lower confidence
GOOD_ACC_M = 10.0         # phone accuracy good enough to tell nearby spots apart
USABLE_ACC_M = 20.0       # beyond this a fix is not used to place a spot

MIN_GAIN = 5.0            # score points (~5 dB of signal or 1 % loss) a move must gain

# Weights of each measurement in the 0-100 spot score.
WEIGHTS = {"signal": 0.30, "loss": 0.25, "latency": 0.20, "bitrate": 0.15, "jitter": 0.10}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def link_metrics(signals: list[float], rtts: list[float | None], bitrates: list[float]) -> dict:
    """Summarise a window: signal samples (dBm), ping results (ms, None = lost), link rates (Mb/s)."""
    ok = [r for r in rtts if r is not None]
    rtt = percentile(ok, 50)
    p90 = percentile(ok, 90)
    return {
        "signal_dbm": percentile(signals, 50),
        "signal_sd": statistics.pstdev(signals) if len(signals) > 1 else None,
        "signal_n": len(signals),
        "rtt_ms": rtt,
        "rtt_p90_ms": p90,
        "jitter_ms": p90 - rtt if ok else None,
        "loss": (len(rtts) - len(ok)) / len(rtts) if rtts else None,
        "ping_n": len(rtts),
        "bitrate_mbps": percentile(bitrates, 50),
    }


def utilities(m: dict) -> dict[str, float]:
    """Map each metric to 0..1 (1 = good). Missing metrics are left out."""
    u = {}
    if m.get("signal_dbm") is not None:
        u["signal"] = _clamp((m["signal_dbm"] + 80) / 30)            # -80 dBm -> 0, -50 dBm -> 1
    if m.get("loss") is not None:
        u["loss"] = _clamp(1 - m["loss"] / 0.05)                     # 0 % -> 1, 5 % -> 0
    if m.get("rtt_ms") is not None:
        u["latency"] = _clamp(1 - math.log10(max(m["rtt_ms"], 2) / 2) / math.log10(50))  # 2 ms -> 1, 100 ms -> 0
    if m.get("jitter_ms") is not None:
        u["jitter"] = _clamp(1 - m["jitter_ms"] / 50)                # 0 -> 1, 50 ms -> 0
    if m.get("bitrate_mbps"):
        u["bitrate"] = _clamp(math.log(max(m["bitrate_mbps"], 6) / 6) / math.log(100))  # 6 -> 0, 600 Mb/s -> 1
    return u


def score(m: dict) -> float | None:
    u = utilities(m)
    if "signal" not in u:
        return None
    total = sum(WEIGHTS[k] for k in u)
    return 100 * sum(WEIGHTS[k] * v for k, v in u.items()) / total


def _block_resample(xs: list, rng: random.Random, block: int) -> list:
    # Consecutive samples are correlated, so resample short runs rather than single values.
    if not xs:
        return []
    out: list = []
    while len(out) < len(xs):
        i = rng.randrange(len(xs))
        out.extend(xs[i:i + block])
    return out[:len(xs)]


def bootstrap_scores(signals, rtts, bitrates, n: int = 300, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        s = score(link_metrics(_block_resample(signals, rng, 3),
                               _block_resample(rtts, rng, 4),
                               _block_resample(bitrates, rng, 3)))
        if s is not None:
            out.append(s)
    return out


def prob_greater(a: list[float], b: list[float]) -> float:
    """P(score A > score B) from two independent bootstrap samples."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.5
    return sum(1.0 if x > y else 0.5 if x == y else 0.0 for x, y in zip(a[:n], b[:n])) / n


def freshness(age_s: float) -> str:
    return "fresh" if age_s < FRESH_S else "aging" if age_s < STALE_S else "stale"


def fmt_age(age_s: float) -> str:
    if age_s < 90:
        return f"{int(age_s)} s"
    if age_s < 90 * 60:
        return f"{round(age_s / 60)} min"
    return f"{age_s / 3600:.1f} h"


def differences(a: dict, b: dict) -> list[dict]:
    """Noteworthy metric differences of A relative to B; `better` is from A's point of view."""
    out = []

    def add(better: bool, text: str):
        out.append({"better": better, "text": text})

    if a["signal_dbm"] is not None and b["signal_dbm"] is not None:
        d = a["signal_dbm"] - b["signal_dbm"]
        if abs(d) >= 3:
            add(d > 0, f"signal {a['signal_dbm']:.0f} dBm vs {b['signal_dbm']:.0f} dBm ({d:+.0f} dB)")
    if a["rtt_ms"] is not None and b["rtt_ms"] is not None:
        d = a["rtt_ms"] - b["rtt_ms"]
        if abs(d) >= 1 and abs(d) >= 0.2 * b["rtt_ms"]:
            add(d < 0, f"latency to router {a['rtt_ms']:.1f} ms vs {b['rtt_ms']:.1f} ms")
    if a["loss"] is not None and b["loss"] is not None:
        d = a["loss"] - b["loss"]
        if abs(d) >= 0.01:
            add(d < 0, f"packet loss {a['loss']:.0%} vs {b['loss']:.0%}")
    if a["jitter_ms"] is not None and b["jitter_ms"] is not None:
        d = a["jitter_ms"] - b["jitter_ms"]
        if abs(d) >= 5:
            add(d < 0, f"latency spikes (p90 − median) {a['jitter_ms']:.0f} ms vs {b['jitter_ms']:.0f} ms")
    if a["bitrate_mbps"] and b["bitrate_mbps"]:
        r = a["bitrate_mbps"] / b["bitrate_mbps"]
        if abs(r - 1) >= 0.2:
            add(r > 1, f"link rate {a['bitrate_mbps']:.0f} Mb/s vs {b['bitrate_mbps']:.0f} Mb/s")
    return out


def _clearly_better(a: dict, b: dict) -> bool:
    """A beats B by a margin worth moving for, and one that window noise does not explain.

    The bootstrap only sees noise inside a ~12 s window, so on its own it calls tiny
    gaps significant; conditions drift more than that over minutes. MIN_GAIN guards that.
    """
    if b["score"] is None:
        return True
    return a["score"] - b["score"] >= MIN_GAIN and prob_greater(a["boot"], b["boot"]) >= 0.9


def recommend(spots: list[dict], now: float) -> dict:
    """Answer "should I move, and where?". Each spot dict has id, label, t_end, metrics,
    score, boot, moved, latency_target. The first spot is where the user started.
    """
    scored = [s for s in spots if s["score"] is not None]
    if len(spots) < 2 or not scored:
        return {"status": "need_more",
                "message": "Measure at least two spots: where you are now, then a candidate."}
    ranked = sorted(scored, key=lambda s: -s["score"])
    best, baseline = ranked[0], spots[0]
    caveats = []

    if best is not baseline and _clearly_better(best, baseline):
        decision, rec, reference, level = "move", best, baseline, 2
        ties = [s["label"] for s in ranked[1:] if s is not baseline and not _clearly_better(best, s)]
        if ties:
            caveats.append(f"{', '.join(ties)} measured about as well as {best['label']}; "
                           "pick whichever is more convenient.")
    else:
        others = [s for s in scored if s is not baseline]
        decision, rec, reference = "stay", baseline, max(others, key=lambda s: s["score"])
        level = 2 if len(others) >= 2 else 1
        if reference["score"] > baseline["score"]:
            caveats.append(f"{reference['label']} measured slightly better "
                           f"(+{reference['score'] - baseline['score']:.0f} points), but the gap is too small "
                           "or too noisy to be worth moving for.")
        if len(others) < 2:
            caveats.append("Only one other spot was measured; try one or two more to be sure.")

    age = now - rec["t_end"]
    if age >= STALE_S:
        level -= 1
        caveats.append(f"{rec['label']} was measured {fmt_age(age)} ago; conditions may have changed. "
                       "Re-measure there before relying on it.")
    if rec["moved"]:
        level -= 1
        caveats.append(f"The phone detected movement while {rec['label']} was being measured.")
    m = rec["metrics"]
    if m["signal_n"] < 6 or m["ping_n"] < 20:
        level -= 1
        caveats.append(f"{rec['label']} has few samples ({m['signal_n']} signal, {m['ping_n']} pings).")
    if len({s["latency_target"] for s in scored}) > 1:
        caveats.append("Latency was measured against different targets at different spots "
                       "(the router did not always answer ping).")

    reasons = differences(rec["metrics"], reference["metrics"])
    if reference["score"] is None:
        reasons.insert(0, {"better": True, "text": f"no usable Wi-Fi link was measured at {reference['label']}"})
    ages = [now - s["t_end"] for s in scored]
    return {
        "status": "ok",
        "decision": decision,
        "spot_id": rec["id"],
        "label": rec["label"],
        "confidence": ["low", "medium", "high"][max(level, 0)],
        "gain": None if reference["score"] is None else round(rec["score"] - reference["score"], 1),
        "reference_label": reference["label"],
        "reasons": reasons,
        "caveats": caveats,
        "ranking": [s["id"] for s in ranked],
        "age_range": [min(ages), max(ages)],
    }


def stronger_ap_hint(aps: list[dict], bssid: str | None, ssid: str | None) -> dict | None:
    """A same-network AP that the scan saw much stronger than the one we were connected to."""
    if not aps or not bssid or not ssid:
        return None
    current = next((a for a in aps if a["bssid"] == bssid), None)
    if current is None:
        return None
    # Same band only: a stronger 2.4 GHz radio is usually not better than a weaker 5 GHz one.
    better = [a for a in aps if a["ssid"] == ssid and a["bssid"] != bssid and a["band"] == current["band"]
              and a["signal_pct"] >= current["signal_pct"] + 15]  # ~9 dB on NM's scale
    if not better:
        return None
    top = max(better, key=lambda a: a["signal_pct"])
    return {"bssid": top["bssid"], "channel": top["channel"], "band": top["band"],
            "signal_dbm": top["signal_dbm"], "current_dbm": current["signal_dbm"]}


# ---------------------------------------------------------------- positions

def position_quality(acc: float | None) -> str:
    if acc is None:
        return "none"
    return "good" if acc <= GOOD_ACC_M else "coarse" if acc <= USABLE_ACC_M else "insufficient"


def local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Metres east/north of an origin. Accurate to well under 1 % over a few hundred metres."""
    return ((lon - lon0) * math.cos(math.radians(lat0)) * 111_320.0,
            (lat - lat0) * 110_574.0)


def fuse_fixes(fixes: list[dict]) -> dict | None:
    """Combine phone fixes taken while the laptop sat still at one spot.

    The position is an accuracy-weighted mean. The reported accuracy is the
    median of the fixes', not shrunk by sqrt(n): consecutive fixes share most
    of their error, so averaging them does not make the position more certain.
    """
    if not fixes:
        return None
    w = [1 / max(f["acc"], 1.0) ** 2 for f in fixes]
    lat = sum(wi * f["lat"] for wi, f in zip(w, fixes)) / sum(w)
    lon = sum(wi * f["lon"] for wi, f in zip(w, fixes)) / sum(w)
    acc = statistics.median(f["acc"] for f in fixes)
    return {"lat": lat, "lon": lon, "acc": round(acc, 1), "n": len(fixes), "quality": position_quality(acc)}


def _median_nn(points: list[tuple[float, float]]) -> float:
    d = [min(math.dist(p, q) for j, q in enumerate(points) if j != i) for i, p in enumerate(points)]
    return statistics.median(d)


def map_layout(spots: list[dict], phone_fix: dict | None = None) -> dict:
    """Decide which maps the collected data supports and lay out geographic positions.

    spots: dicts with id, label, position (fused fix or None), canvas ({x, y} in 0..1 or None).
    """
    out: dict = {}
    geo = [s for s in spots if s["position"] and s["position"]["acc"] <= USABLE_ACC_M]
    out["unplaced_geo"] = [{"id": s["id"], "label": s["label"],
                            "acc": s["position"]["acc"] if s["position"] else None}
                           for s in spots if s not in geo]
    if len(geo) >= 2:
        lat0 = statistics.fmean(s["position"]["lat"] for s in geo)
        lon0 = statistics.fmean(s["position"]["lon"] for s in geo)
        pts = []
        for s in geo:
            x, y = local_xy(s["position"]["lat"], s["position"]["lon"], lat0, lon0)
            pts.append({"id": s["id"], "x": x, "y": y, "acc": s["position"]["acc"]})
        nn = _median_nn([(p["x"], p["y"]) for p in pts])
        acc = statistics.median(p["acc"] for p in pts)
        resolvable = acc <= 0.5 * nn
        interp = len(pts) >= 4 and acc <= nn / 3
        if not resolvable:
            note = (f"Phone accuracy (±{acc:.0f} m) is large compared with the spacing between spots "
                    f"(~{nn:.0f} m), so their relative positions are not reliable.")
        elif not interp:
            note = ("Coverage interpolation needs at least 4 placed spots spaced well beyond "
                    f"the position uncertainty (±{acc:.0f} m).")
        else:
            note = ""
        geo_out = {"points": pts, "spacing_m": round(nn, 1), "acc_m": acc, "resolvable": resolvable,
                   "interpolation": interp, "radius_m": max(0.75 * nn, 2 * acc), "note": note}
        if phone_fix:
            x, y = local_xy(phone_fix["lat"], phone_fix["lon"], lat0, lon0)
            geo_out["phone"] = {"x": x, "y": y, "acc": phone_fix["acc"]}
        out["geo"] = geo_out
    else:
        out["geo"] = None

    placed = [s for s in spots if s["canvas"]]
    out["canvas"] = {"placed": len(placed), "interpolation": len(placed) >= 4}
    return out


def settled_check(live: dict, spot_metrics: dict, spot_boot: list[float]) -> dict:
    """Does the connection now, at the chosen spot, still match what the survey measured there?"""
    live_score = score(live)
    low = percentile(spot_boot, 10)
    return {
        "live_score": live_score,
        "spot_score": score(spot_metrics),
        "consistent": live_score is not None and low is not None and live_score >= low - 5,
        "differences": differences(live, spot_metrics),
    }


def channel_relations(aps: list[dict], bssid: str | None) -> dict:
    """How other APs share spectrum with the connected one: same primary channel or overlapping span."""
    me = next((a for a in aps if a["bssid"] == bssid), None)
    rel: dict[str, str] = {}
    if me:
        for a in aps:
            if a is me or a["band"] != me["band"]:
                continue
            if a["channel"] == me["channel"]:
                rel[a["bssid"]] = "same"
            elif a["span"][0] < me["span"][1] and me["span"][0] < a["span"][1]:
                rel[a["bssid"]] = "overlap"
    return rel
