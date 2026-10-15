"""Optional phone link. The phone page reports its location and whether it is moving."""

from __future__ import annotations

import hmac
import math
import secrets
import threading
from collections import deque

from .analysis import position_quality

MAX_FIX_AGE = 20.0   # seconds a fix stays usable without a newer one


class Phone:
    def __init__(self):
        self.token = secrets.token_urlsafe(16)
        self.url: str | None = None
        self.reports: deque[dict] = deque(maxlen=3600)
        self.last_seen: float | None = None
        self._lock = threading.Lock()

    def check(self, token: str) -> bool:
        return hmac.compare_digest(token.encode(), self.token.encode())

    def rotate(self) -> None:
        """Invalidate the current pairing link and forget everything the phone sent."""
        with self._lock:
            self.token = secrets.token_urlsafe(16)
            self.url = None
            self.reports.clear()
            self.last_seen = None

    def report(self, data: dict, now: float) -> None:
        fix = None
        f = data.get("fix") if isinstance(data, dict) else None
        if isinstance(f, dict):
            try:
                lat, lon, acc = float(f["lat"]), float(f["lon"]), float(f["acc"])
                ts = float(f["ts"])
                age = max(0.0, float(f.get("age_ms", 0)) / 1000)
            except (KeyError, TypeError, ValueError):
                lat = math.nan
            if all(map(math.isfinite, (lat, lon, acc, ts, age))) and -90 <= lat <= 90 \
                    and -180 <= lon <= 180 and 0 < acc < 100_000 and age < 120:
                fix = {"lat": lat, "lon": lon, "acc": acc, "ts": ts, "t": now - age}
        with self._lock:
            self.reports.append({"t": now, "fix": fix, "moving": bool(data.get("moving"))})
            self.last_seen = now

    def fixes_between(self, t0: float, t1: float, max_age: float = MAX_FIX_AGE) -> list[dict]:
        """Distinct fixes the phone reported as current during [t0, t1].

        A still phone may not produce new fixes, so its latest one stays valid
        for a while; the phone resends it each second.
        """
        with self._lock:
            seen, out = set(), []
            for r in self.reports:
                f = r["fix"]
                if f and t0 <= r["t"] <= t1 and r["t"] - f["t"] <= max_age and f["ts"] not in seen:
                    seen.add(f["ts"])
                    out.append(f)
            return out

    def moved_between(self, t0: float, t1: float) -> bool:
        with self._lock:
            return any(r["moving"] for r in self.reports if t0 <= r["t"] <= t1)

    def latest_fix(self, now: float) -> dict | None:
        """The phone's current fix, if the phone is still reporting and the fix is recent."""
        with self._lock:
            r = self.reports[-1] if self.reports else None
        if r and r["fix"] and now - r["t"] <= 5 and now - r["fix"]["t"] <= MAX_FIX_AGE:
            return r["fix"]
        return None

    def status(self, now: float) -> dict:
        fix = self.latest_fix(now)
        return {
            "url": self.url,
            "connected": self.last_seen is not None and now - self.last_seen < 5,
            "last_seen_age": now - self.last_seen if self.last_seen else None,
            "acc": fix["acc"] if fix else None,
            "quality": position_quality(fix["acc"] if fix else None),
        }
