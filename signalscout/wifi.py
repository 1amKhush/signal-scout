"""Readers for the laptop's Wi-Fi state.

Everything here shells out to standard tools (nmcli, ip, iwconfig) or reads
/proc and /sys, and the parsing is split from the I/O so it can be tested
against captured output.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- nmcli scan

def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output; ':' and '\\' inside values are backslash-escaped."""
    fields, cur, escaped = [], [], False
    for ch in line:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def pct_to_dbm(pct: float) -> float:
    """Invert NetworkManager's quality mapping (-100 dBm -> 0 %, -40 dBm -> 100 %). Approximate."""
    return round(-100 + 0.6 * pct, 1)


def band_of(freq: int) -> str:
    if freq < 2500:
        return "2.4"
    if freq < 5925:
        return "5"
    return "6"


def channel_span(freq: int, channel: int, width: int) -> tuple[int, int]:
    """Occupied frequency range (MHz) of an AP, accounting for channel bonding.

    nmcli reports the primary channel; a 40/80/160 MHz BSS occupies an aligned
    block around it. For 2.4 GHz HT40 the secondary side is not reported, so
    the usual convention (above for channels <= 7, below otherwise) is assumed.
    """
    if width <= 20:
        return freq - 10, freq + 10
    band = band_of(freq)
    if band == "2.4":
        lo = freq - 10 if channel <= 7 else freq - 30
        return lo, lo + 40
    n20 = width // 20
    if band == "5":
        base = 149 if channel >= 149 else 36
        first = base + ((channel - base) // (4 * n20)) * 4 * n20
        center = 5000 + 5 * (first + 2 * (n20 - 1))
    else:
        first = 1 + ((channel - 1) // (4 * n20)) * 4 * n20
        center = 5950 + 5 * (first + 2 * (n20 - 1))
    return center - width // 2, center + width // 2


SCAN_FIELDS = ["IN-USE", "BSSID", "SSID", "CHAN", "FREQ", "RATE", "BANDWIDTH", "SIGNAL", "SECURITY"]


def _leading_int(text: str) -> int | None:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def parse_scan(text: str, fields: list[str]) -> list[dict]:
    aps = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = dict(zip(fields, split_terse(line)))
        freq = _leading_int(row.get("FREQ", ""))
        chan = _leading_int(row.get("CHAN", ""))
        pct = _leading_int(row.get("SIGNAL", ""))
        if freq is None or chan is None or pct is None:
            continue
        width = _leading_int(row.get("BANDWIDTH", ""))
        lo, hi = channel_span(freq, chan, width or 20)
        aps.append({
            "bssid": row.get("BSSID", "").upper(),
            "ssid": row.get("SSID", ""),
            "in_use": row.get("IN-USE", "").strip() == "*",
            "channel": chan,
            "freq": freq,
            "band": band_of(freq),
            "width": width,
            "rate_mbps": _leading_int(row.get("RATE", "")),
            "signal_pct": pct,
            "signal_dbm": pct_to_dbm(pct),
            "security": row.get("SECURITY", "").strip() or "open",
            "span": [lo, hi],
        })
    aps.sort(key=lambda a: -a["signal_pct"])
    return aps


def scan(iface: str, rescan: str = "no") -> list[dict]:
    """List APs from NetworkManager. rescan='no' reads NM's cache without disturbing the link."""
    for fields in (SCAN_FIELDS, [f for f in SCAN_FIELDS if f != "BANDWIDTH"]):
        p = _run(["nmcli", "-t", "-f", ",".join(fields), "dev", "wifi", "list",
                  "ifname", iface, "--rescan", rescan], timeout=30)
        if p and p.returncode == 0:
            return parse_scan(p.stdout, fields)
    return []


def request_rescan(iface: str) -> bool:
    p = _run(["nmcli", "dev", "wifi", "rescan", "ifname", iface])
    return bool(p and p.returncode == 0)


def find_iface() -> str | None:
    p = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"])
    if p and p.returncode == 0:
        rows = [split_terse(line) for line in p.stdout.splitlines()]
        wifi = [r for r in rows if len(r) >= 3 and r[1] == "wifi"]
        for r in wifi:
            if r[2].startswith("connected"):
                return r[0]
        if wifi:
            return wifi[0][0]
    found = sorted(d.parent.name for d in Path("/sys/class/net").glob("*/wireless"))
    return found[0] if found else None


# ---------------------------------------------------------------- link state

def parse_proc_wireless(text: str, iface: str) -> dict | None:
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        if name.strip() != iface:
            continue
        parts = rest.split()
        if len(parts) < 10:
            return None
        num = lambda s: float(s.rstrip("."))  # noqa: E731  values carry a trailing '.' when updated
        level, noise = num(parts[2]), num(parts[3])
        return {
            "quality": num(parts[1]),
            "signal_dbm": level if -200 < level < 0 else None,
            "noise_dbm": noise if -200 < noise < 0 else None,
            "discard_retry": int(parts[7]),
            "missed_beacon": int(parts[9]),
        }
    return None


def parse_iwconfig(text: str) -> dict:
    out: dict = {}
    if m := re.search(r"Access Point:\s*([0-9A-Fa-f:]{17})", text):
        out["bssid"] = m.group(1).upper()
    if m := re.search(r"Bit Rate[=:]\s*([\d.]+)\s*([GMk])b/s", text):
        out["bitrate_mbps"] = float(m.group(1)) * {"G": 1000, "M": 1, "k": 0.001}[m.group(2)]
    if m := re.search(r"Tx-Power[=:]\s*(-?\d+)\s*dBm", text):
        out["tx_power_dbm"] = int(m.group(1))
    if m := re.search(r"Tx excessive retries:(\d+)", text):
        out["tx_excessive_retries"] = int(m.group(1))
    if m := re.search(r"Power Management:(\w+)", text):
        out["power_save"] = m.group(1) == "on"
    return out


_HAS_IWCONFIG = shutil.which("iwconfig") is not None


def read_link(iface: str) -> dict:
    """One sample of the current link. Missing values are simply absent/None."""
    sample: dict = {}
    try:
        sample.update(parse_proc_wireless(Path("/proc/net/wireless").read_text(), iface) or {})
    except OSError:
        pass
    if _HAS_IWCONFIG:
        p = _run(["iwconfig", iface], timeout=3)
        if p and p.returncode == 0:
            sample.update(parse_iwconfig(p.stdout))
    stats = Path("/sys/class/net") / iface / "statistics"
    for name in ("rx_bytes", "tx_bytes"):
        try:
            sample[name] = int((stats / name).read_text())
        except (OSError, ValueError):
            pass
    return sample


def default_gateway(iface: str) -> str | None:
    p = _run(["ip", "-4", "route", "show", "default", "dev", iface])
    m = re.search(r"default via (\S+)", p.stdout) if p else None
    return m.group(1) if m else None


def iface_ip(iface: str) -> str | None:
    p = _run(["ip", "-4", "-o", "addr", "show", "dev", iface])
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", p.stdout) if p else None
    return m.group(1) if m else None
