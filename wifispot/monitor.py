"""Live sampling of the laptop's link: signal and link rate at 1 Hz, plus continuous ping."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import Counter, deque

from . import wifi
from .analysis import link_metrics, percentile

_REPLY = re.compile(r"icmp_seq=(\d+)\b.*\btime=([\d.]+) ms")
_SEQ = re.compile(r"icmp_seq=(\d+)\b")


class Pinger:
    """Runs ping(8) against one target and records every probe as (t_sent, rtt_ms or None for lost)."""

    TIMEOUT = 2.0      # a reply slower than this counts as lost

    def __init__(self, target: str, interval: float = 0.25, start: bool = True):
        # 4 probes/s to the router gives ~50 samples per measurement window;
        # the unprivileged minimum is 0.2 s.
        self.target = target
        self.interval = interval
        self.results: deque[tuple[float, float | None]] = deque(maxlen=4 * 3600)
        self.error: str | None = None
        self._pending: dict[int, float] = {}
        self._last_seq: int | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        if start:
            threading.Thread(target=self._run, daemon=True, name=f"ping-{target}").start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def _run(self) -> None:
        while not self._stop.is_set():
            # -O prints "no answer yet" for unanswered probes so losses are seen live.
            # -c bounds the run so sequence numbers never wrap; the loop restarts it.
            cmd = ["ping", "-n", "-O", "-i", str(self.interval), "-W", str(int(self.TIMEOUT)),
                   "-c", "14400", self.target]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                              text=True, bufsize=1)
            except OSError as e:
                self.error = f"cannot run ping: {e}"
                return
            for line in self._proc.stdout:
                self.feed(line, time.time())
            self._proc.wait()
            with self._lock:
                self._expire(float("inf"))
                self._last_seq = None
            self._stop.wait(1.0)

    def feed(self, line: str, now: float) -> None:
        with self._lock:
            if m := _REPLY.search(line):
                seq, rtt = int(m.group(1)), float(m.group(2))
                self._see(seq, now - rtt / 1000)
                sent = self._pending.pop(seq, None)
                if sent is not None:          # None: duplicate or reply after the timeout
                    self.results.append((sent, rtt))
                self.error = None
            elif m := _SEQ.search(line):      # "no answer yet", "Destination Host Unreachable", ...
                self._see(int(m.group(1)), now - self.interval)
                if "no answer yet" not in line:
                    self.error = line.strip()
            elif line.strip() and not line.startswith(("PING", "---", "rtt", "round-trip")) \
                    and "packets transmitted" not in line:
                self.error = line.strip()
            self._expire(now)

    def _see(self, seq: int, t: float) -> None:
        if self._last_seq is None or seq > self._last_seq:
            first = seq if self._last_seq is None else self._last_seq + 1
            for s in range(first, seq + 1):
                self._pending.setdefault(s, t)
            self._last_seq = seq

    def _expire(self, now: float) -> None:
        for seq, sent in list(self._pending.items()):
            if now - sent > self.TIMEOUT + 0.5:
                del self._pending[seq]
                self.results.append((sent, None))

    def window(self, t0: float, t1: float) -> list[float | None]:
        with self._lock:
            return [r for t, r in sorted(self.results, key=lambda x: x[0]) if t0 <= t <= t1]

    def buckets(self, t0: float) -> list[dict]:
        """Per-second summary since t0, for charts."""
        with self._lock:
            res = [(t, r) for t, r in self.results if t >= t0]
        by_sec: dict[int, list] = {}
        for t, r in res:
            by_sec.setdefault(int(t), []).append(r)
        out = []
        for sec in sorted(by_sec):
            rs = by_sec[sec]
            ok = [r for r in rs if r is not None]
            out.append({"t": sec, "rtt": percentile(ok, 50), "max": max(ok) if ok else None,
                        "lost": len(rs) - len(ok), "n": len(rs)})
        return out

    def status(self, now: float) -> dict:
        recent = self.window(now - 10, now)
        answering = any(r is not None for r in recent)
        return {"target": self.target, "answering": answering,
                "error": None if answering else self.error}


class Monitor:
    """Background sampler for one Wi-Fi interface."""

    def __init__(self, iface: str, internet_host: str | None = "1.1.1.1"):
        self.iface = iface
        self.link: deque[dict] = deque(maxlen=3600)
        self.aps: list[dict] = []
        self.aps_at: float | None = None
        self.gateway: str | None = None
        self.gw: Pinger | None = None
        self.inet: Pinger | None = Pinger(internet_host, interval=1.0) if internet_host else None
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._prev: dict | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="monitor").start()

    def stop(self) -> None:
        for p in (self.gw, self.inet):
            if p:
                p.stop()

    def _loop(self) -> None:
        tick, next_t = 0, time.time()
        while True:
            if tick % 10 == 0:
                self._refresh_gateway()
            if tick % 5 == 0 and not self._scan_lock.locked():
                self._refresh_aps()
            self._sample(time.time())
            tick += 1
            next_t = max(next_t + 1.0, time.time())
            time.sleep(max(0.0, next_t - time.time()))

    def _sample(self, now: float) -> None:
        s = wifi.read_link(self.iface)
        s["t"] = now
        prev = self._prev
        if prev and "rx_bytes" in s and "rx_bytes" in prev and now > prev["t"]:
            dt = now - prev["t"]
            s["rx_bps"] = max(0.0, (s["rx_bytes"] - prev["rx_bytes"]) * 8 / dt)
            s["tx_bps"] = max(0.0, (s["tx_bytes"] - prev["tx_bytes"]) * 8 / dt)
        self._prev = s
        with self._lock:
            self.link.append(s)

    def _refresh_gateway(self) -> None:
        gw = wifi.default_gateway(self.iface)
        if gw != self.gateway:
            if self.gw:
                self.gw.stop()
            self.gateway = gw
            self.gw = Pinger(gw) if gw else None

    def _refresh_aps(self) -> None:
        aps = wifi.scan(self.iface, "no")
        with self._lock:
            self.aps, self.aps_at = aps, time.time()

    def rescan(self, wait: float = 4.0) -> list[dict]:
        """Ask NetworkManager for a fresh scan. Scanning takes the radio off-channel briefly,
        so this only runs on request, never on a timer."""
        with self._scan_lock:
            wifi.request_rescan(self.iface)
            time.sleep(wait)
            self._refresh_aps()
        return self.aps

    def connection(self) -> dict:
        with self._lock:
            last = self.link[-1] if self.link else {}
            aps = self.aps
        bssid = last.get("bssid")
        ap = next((a for a in aps if a["bssid"] == bssid), None) or next((a for a in aps if a["in_use"]), None)
        out = dict(ap or {})
        if bssid:
            out["bssid"] = bssid
        return out

    def window(self, t0: float, t1: float) -> dict:
        """Raw samples of a measurement window."""
        with self._lock:
            link = [s for s in self.link if t0 <= s["t"] <= t1]
        rtts = self.gw.window(t0, t1) if self.gw else []
        target = "gateway"
        if not any(r is not None for r in rtts) and self.inet:
            alt = self.inet.window(t0, t1)
            if any(r is not None for r in alt):
                rtts, target = alt, "internet"
        bssids = Counter(s["bssid"] for s in link if s.get("bssid"))
        return {
            "signals": [s["signal_dbm"] for s in link if s.get("signal_dbm") is not None],
            "bitrates": [s["bitrate_mbps"] for s in link if s.get("bitrate_mbps")],
            "rtts": rtts,
            "latency_target": target,
            "bssid": bssids.most_common(1)[0][0] if bssids else None,
            "roamed": len(bssids) > 1,
        }

    def snapshot(self, span: float = 300) -> dict:
        now = time.time()
        with self._lock:
            link = [s for s in self.link if s["t"] >= now - span]
            aps_at = self.aps_at
        last = link[-1] if link else {}
        recent = self.window(now - 60, now)
        return {
            "t": now,
            "iface": self.iface,
            "connection": self.connection(),
            "aps_age": now - aps_at if aps_at else None,
            "link": [{"t": s["t"], "signal": s.get("signal_dbm"), "bitrate": s.get("bitrate_mbps"),
                      "bssid": s.get("bssid"), "rx": s.get("rx_bps"), "tx": s.get("tx_bps")} for s in link],
            "radio": {k: last.get(k) for k in ("quality", "noise_dbm", "tx_power_dbm", "missed_beacon",
                                               "discard_retry", "tx_excessive_retries", "power_save",
                                               "rx_bps", "tx_bps")},
            "gateway": {**self.gw.status(now), "series": self.gw.buckets(now - span)} if self.gw else None,
            "internet": {**self.inet.status(now), "series": self.inet.buckets(now - span)} if self.inet else None,
            "last60": {**link_metrics(recent["signals"], recent["rtts"], recent["bitrates"]),
                       "latency_target": recent["latency_target"]},
        }
