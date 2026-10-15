"""Command-line entry point: `signalscout` (or `python -m signalscout`)."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

from . import wifi
from .monitor import Monitor
from .phone import Phone
from .server import App, Server
from .survey import SurveyManager


def main() -> None:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    p = argparse.ArgumentParser(prog="signalscout", description="Find a better Wi-Fi spot on a Linux laptop.")
    p.add_argument("--iface", help="Wi-Fi interface (default: the connected one)")
    p.add_argument("--port", type=int, default=8765, help="laptop UI port on 127.0.0.1 (default 8765)")
    p.add_argument("--phone-port", type=int, default=8766, help="HTTPS port for the phone page (default 8766)")
    p.add_argument("--window", type=float, default=12, help="seconds per measurement window (default 12)")
    p.add_argument("--internet-host", default="1.1.1.1",
                   help="host pinged for end-to-end latency; empty to disable (default 1.1.1.1)")
    p.add_argument("--data-dir", type=Path, default=data_home / "signalscout")
    p.add_argument("--no-browser", action="store_true", help="do not open the UI in a browser")
    args = p.parse_args()

    if not shutil.which("nmcli"):
        sys.exit("SignalScout needs NetworkManager's nmcli to discover access points.")
    iface = args.iface or wifi.find_iface()
    if not iface:
        sys.exit("No Wi-Fi interface found. Pass one with --iface.")

    monitor = Monitor(iface, args.internet_host or None)
    monitor.start()
    phone = Phone()
    surveys = SurveyManager(monitor, phone, args.data_dir, args.window)
    app = App(monitor, surveys, phone, args.data_dir, args.port, args.phone_port)
    try:
        server = Server(("127.0.0.1", args.port), app)
    except OSError as e:
        sys.exit(f"Cannot listen on 127.0.0.1:{args.port}: {e.strerror}. Is SignalScout already running?")

    url = f"http://127.0.0.1:{args.port}/"
    print(f"SignalScout: monitoring {iface}. Open {url}  (Ctrl+C to quit)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_phone()
        monitor.stop()


if __name__ == "__main__":
    main()
