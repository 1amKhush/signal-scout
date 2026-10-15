"""Survey sessions: short stationary measurement windows at candidate spots, kept as JSON."""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import uuid
import zlib
from pathlib import Path

from . import analysis


class Busy(Exception):
    pass


@dataclasses.dataclass
class Spot:
    id: str
    label: str
    t_start: float
    t_end: float
    signals: list[float]
    rtts: list[float | None]
    bitrates: list[float]
    latency_target: str
    bssid: str | None
    roamed: bool
    aps: list[dict]
    position: dict | None = None     # fused phone fix, see analysis.fuse_fixes
    canvas: dict | None = None       # user placement {x, y} in 0..1
    moved: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> Spot:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _slim_ap(a: dict) -> dict:
    return {k: a[k] for k in ("bssid", "ssid", "channel", "band", "width", "signal_pct", "signal_dbm")}


class SurveyManager:
    def __init__(self, monitor, phone, data_dir: Path, window_s: float = 12):
        self.monitor = monitor
        self.phone = phone
        self.window_s = window_s
        self.dir = data_dir / "surveys"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.job: dict | None = None
        self._cancel = threading.Event()
        self._boot: dict[str, list[float]] = {}
        self.survey = self._load_latest() or self._fresh()

    # ------------------------------------------------------------ persistence

    def _fresh(self) -> dict:
        return {"id": time.strftime("%Y%m%d-%H%M%S"), "created": time.time(), "spots": [], "chosen_id": None, "chosen_at": None}

    def _load_latest(self) -> dict | None:
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        try:
            d = json.loads(files[-1].read_text())
            d["spots"] = [Spot.from_dict(s) for s in d["spots"]]
            return d
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self) -> None:
        d = {**self.survey, "spots": [dataclasses.asdict(s) for s in self.survey["spots"]]}
        path = self.dir / f"{self.survey['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(path)

    # ------------------------------------------------------------ actions

    def new(self) -> None:
        with self.lock:
            if self.job:
                raise Busy("A measurement is in progress.")
            self.survey = self._fresh()
            self._save()

    def measure(self, label: str = "") -> None:
        with self.lock:
            if self.job:
                raise Busy("A measurement is already in progress.")
            n = len(self.survey["spots"]) + 1
            label = label.strip()[:60] or ("Start" if n == 1 else f"Spot {n}")
            self._cancel.clear()
            self.job = {"phase": "measuring", "label": label, "t0": time.time(), "duration": self.window_s}
        threading.Thread(target=self._measure, args=(label,), daemon=True, name="measure").start()

    def cancel(self) -> None:
        self._cancel.set()

    def _measure(self, label: str) -> None:
        try:
            t0 = self.job["t0"]
            if self._cancel.wait(self.window_s):
                return
            t1 = time.time()
            with self.lock:
                self.job["phase"] = "scanning"
            # Scan after the window so off-channel scanning cannot disturb the latency samples.
            # The wait also lets the last pings of the window reach their timeout.
            aps = self.monitor.rescan()
            if self._cancel.is_set():
                return
            win = self.monitor.window(t0, t1)
            spot = Spot(
                id=uuid.uuid4().hex[:8], label=label, t_start=t0, t_end=t1,
                signals=win["signals"], rtts=win["rtts"], bitrates=win["bitrates"],
                latency_target=win["latency_target"], bssid=win["bssid"], roamed=win["roamed"],
                aps=[_slim_ap(a) for a in aps],
                position=analysis.fuse_fixes(self.phone.fixes_between(t0 - 2, t1 + 1)),
                moved=self.phone.moved_between(t0, t1),
            )
            with self.lock:
                self.survey["spots"].append(spot)
                self._save()
        finally:
            with self.lock:
                self.job = None

    def _spot(self, spot_id: str) -> Spot:
        for s in self.survey["spots"]:
            if s.id == spot_id:
                return s
        raise KeyError(spot_id)

    def update_spot(self, spot_id: str, data: dict) -> None:
        with self.lock:
            s = self._spot(spot_id)
            if "label" in data:
                label = str(data["label"]).strip()[:60]
                if label:
                    s.label = label
            if "canvas" in data:
                c = data["canvas"]
                if c is None:
                    s.canvas = None
                else:
                    x, y = float(c["x"]), float(c["y"])
                    if not (0 <= x <= 1 and 0 <= y <= 1):
                        raise ValueError("canvas position must be within 0..1")
                    s.canvas = {"x": x, "y": y}
            self._save()

    def delete_spot(self, spot_id: str) -> None:
        with self.lock:
            self.survey["spots"].remove(self._spot(spot_id))
            if self.survey["chosen_id"] == spot_id:
                self.survey["chosen_id"] = None
            self._save()

    def choose(self, spot_id: str | None) -> None:
        with self.lock:
            if spot_id is not None:
                self._spot(spot_id)
            self.survey["chosen_id"] = spot_id
            self.survey["chosen_at"] = time.time() if spot_id else None
            self._save()

    # ------------------------------------------------------------ views

    def spot_analysis(self, s: Spot) -> tuple[dict, float | None, list[float]]:
        m = analysis.link_metrics(s.signals, s.rtts, s.bitrates)
        if s.id not in self._boot:   # measurements never change after recording
            self._boot[s.id] = analysis.bootstrap_scores(s.signals, s.rtts, s.bitrates, seed=zlib.crc32(s.id.encode()))
        return m, analysis.score(m), self._boot[s.id]

    def chosen(self) -> tuple[Spot, dict, list[float], float] | None:
        """The spot the user settled at, its survey metrics, and when they settled."""
        with self.lock:
            cid = self.survey["chosen_id"]
            if not cid:
                return None
            s = self._spot(cid)
            m, _, boot = self.spot_analysis(s)
            return s, m, boot, self.survey.get("chosen_at") or 0.0

    def baseline(self) -> tuple[Spot, dict] | None:
        with self.lock:
            if not self.survey["spots"]:
                return None
            s = self.survey["spots"][0]
            return s, self.spot_analysis(s)[0]

    def state(self, now: float) -> dict:
        with self.lock:
            spots = list(self.survey["spots"])
            job = dict(self.job) if self.job else None
            chosen_id = self.survey["chosen_id"]
        views, rec_in = [], []
        for s in spots:
            m, sc, boot = self.spot_analysis(s)
            age = now - s.t_end
            ap = next((a for a in s.aps if a["bssid"] == s.bssid), None)
            views.append({
                "id": s.id, "label": s.label, "t_end": s.t_end, "age": age,
                "freshness": analysis.freshness(age), "metrics": m,
                "score": sc, "score_range": [analysis.percentile(boot, 10), analysis.percentile(boot, 90)],
                "bssid": s.bssid, "ap": ap, "roamed": s.roamed, "moved": s.moved,
                "latency_target": s.latency_target, "position": s.position, "canvas": s.canvas,
                "hint": analysis.stronger_ap_hint(s.aps, s.bssid, ap["ssid"] if ap else None),
            })
            rec_in.append({"id": s.id, "label": s.label, "t_end": s.t_end, "metrics": m, "score": sc,
                           "boot": boot, "moved": s.moved, "latency_target": s.latency_target})
        if job:
            job["remaining"] = max(0.0, job["t0"] + job["duration"] - now)
        return {
            "id": self.survey["id"],
            "window_s": self.window_s,
            "job": job,
            "chosen_id": chosen_id,
            "spots": views,
            "recommendation": analysis.recommend(rec_in, now),
            "map": analysis.map_layout(views, self.phone.latest_fix(now)),
        }
