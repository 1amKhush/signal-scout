"""HTTP servers.

* The laptop UI and its API listen on 127.0.0.1 only.
* The phone page listens on the Wi-Fi interface's address over HTTPS (needed for
  browser geolocation), only after the user asks to pair, and every route is
  gated by the random token in the QR code.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import segno

from . import analysis, tls, wifi
from .survey import Busy

STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
PHONE_ASSETS = {"phone.js", "phone.css"}
MAX_BODY = 64 * 1024
SETTLE_MIN_S = 15   # live data needed at the chosen spot before comparing it with the survey


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, app, tls_ctx: ssl.SSLContext | None = None):
        self.app = app
        self.tls = tls_ctx
        super().__init__(addr, Handler)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
            return   # e.g. the phone's browser rejecting the self-signed cert before the user accepts it
        super().handle_error(request, client_address)


class App:
    def __init__(self, monitor, surveys, phone, data_dir: Path, port: int, phone_port: int):
        self.monitor = monitor
        self.surveys = surveys
        self.phone = phone
        self.data_dir = data_dir
        self.port = port
        self.phone_port = phone_port
        self.phone_server: Server | None = None
        self._pair_lock = threading.Lock()

    # ------------------------------------------------------------ views

    def live(self) -> dict:
        snap = self.monitor.snapshot()
        chosen = self.surveys.chosen()
        if chosen:
            spot, m, boot, since = chosen
            now = snap["t"]
            start = max(since, now - 60)   # only samples taken since the user settled here
            w = self.monitor.window(start, now)
            live = analysis.link_metrics(w["signals"], w["rtts"], w["bitrates"])
            settled = {"spot_id": spot.id, "label": spot.label, "spot_age": now - spot.t_end,
                       "window_s": now - start, "pending": now - start < SETTLE_MIN_S}
            if not settled["pending"]:
                settled.update(analysis.settled_check(live, m, boot))
                base = self.surveys.baseline()
                if base and base[0].id != spot.id:
                    settled["baseline_label"] = base[0].label
                    settled["vs_baseline"] = analysis.differences(live, base[1])
            snap["settled"] = settled
        return snap

    def aps(self) -> dict:
        conn = self.monitor.connection()
        aps = self.monitor.aps
        return {"aps": aps, "age": time.time() - self.monitor.aps_at if self.monitor.aps_at else None,
                "connection": conn, "relations": analysis.channel_relations(aps, conn.get("bssid"))}

    def survey(self) -> dict:
        now = time.time()
        return {**self.surveys.state(now), "phone": self.phone.status(now)}

    def phone_state(self) -> dict:
        st = self.surveys.state(time.time())
        rec = st["recommendation"]
        out = {"job": st["job"], "spots": len(st["spots"]), "window_s": st["window_s"], "recommendation": None}
        if rec["status"] == "ok":
            spot = next(s for s in st["spots"] if s["id"] == rec["spot_id"])
            out["recommendation"] = {"label": rec["label"], "decision": rec["decision"],
                                     "confidence": rec["confidence"], "position": spot["position"]}
        return out

    # ------------------------------------------------------------ pairing

    def pair(self) -> dict:
        ip = wifi.iface_ip(self.monitor.iface)
        if not ip:
            raise ApiError(409, "The laptop has no IPv4 address on Wi-Fi, so a phone cannot reach it.")
        with self._pair_lock:
            if self.phone_server and self.phone_server.server_address[0] != ip:
                self.stop_phone()
            if not self.phone_server:
                cert, key = tls.ensure_cert(self.data_dir / "tls", ip)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
                try:
                    self.phone_server = Server((ip, self.phone_port), self, ctx)
                except OSError as e:
                    raise ApiError(409, f"Cannot listen on {ip}:{self.phone_port}: {e.strerror}") from e
                threading.Thread(target=self.phone_server.serve_forever, daemon=True, name="phone").start()
        self.phone.url = f"https://{ip}:{self.phone_port}/p/{self.phone.token}"
        qr = segno.make(self.phone.url, error="m").svg_inline(scale=5, border=2, dark="#0b0b0b", light="#ffffff")
        return {"url": self.phone.url, "qr_svg": qr}

    def unpair(self) -> None:
        self.phone.rotate()
        with self._pair_lock:
            self.stop_phone()

    def stop_phone(self) -> None:
        if self.phone_server:
            self.phone_server.shutdown()
            self.phone_server.server_close()
            self.phone_server = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wifispot"
    timeout = 30
    server: Server

    def setup(self):
        if self.server.tls:
            self.request.settimeout(15)
            self.request = self.server.tls.wrap_socket(self.request, server_side=True)
        super().setup()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ------------------------------------------------------------ plumbing

    def _dispatch(self, method: str) -> None:
        try:
            path = urlsplit(self.path).path
            if self.server.tls:
                self._phone_routes(method, path)
            else:
                self._local_routes(method, path)
        except ApiError as e:
            self._json({"error": e.message}, e.status)
        except Busy as e:
            self._json({"error": str(e)}, 409)
        except (KeyError, ValueError, TypeError) as e:
            self._json({"error": f"bad request: {e}"}, 400)

    def _body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            # Forces cross-site requests into a CORS preflight that this server never approves.
            raise ApiError(415, "expected application/json")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError(413, "body too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ApiError(400, "expected a JSON object")
        return data

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _static(self, name: str) -> None:
        path = STATIC / name
        if not re.fullmatch(r"[\w.-]+", name) or not path.is_file():
            raise ApiError(404, "not found")
        self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    # ------------------------------------------------------------ laptop UI

    def _local_routes(self, method: str, path: str) -> None:
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise ApiError(403, "forbidden host")   # blocks DNS-rebinding pages from reaching the API
        app = self.server.app
        surveys = app.surveys
        if method == "GET":
            if path == "/":
                return self._static("index.html")
            if m := re.fullmatch(r"/static/([\w.-]+)", path):
                return self._static(m.group(1))
            if path == "/api/live":
                return self._json(app.live())
            if path == "/api/aps":
                return self._json(app.aps())
            if path == "/api/survey":
                return self._json(app.survey())
        elif method == "POST":
            data = self._body()
            if path == "/api/aps/rescan":
                threading.Thread(target=app.monitor.rescan, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/survey/new":
                surveys.new()
                return self._json({"ok": True})
            if path == "/api/survey/measure":
                surveys.measure(str(data.get("label", "")))
                return self._json({"ok": True})
            if path == "/api/survey/cancel":
                surveys.cancel()
                return self._json({"ok": True})
            if path == "/api/survey/choose":
                surveys.choose(data.get("spot_id"))
                return self._json({"ok": True})
            if path == "/api/phone/pair":
                return self._json(app.pair())
            if path == "/api/phone/unpair":
                app.unpair()
                return self._json({"ok": True})
        elif m := re.fullmatch(r"/api/survey/spots/(\w+)", path):
            if method == "PATCH":
                surveys.update_spot(m.group(1), self._body())
                return self._json({"ok": True})
            if method == "DELETE":
                surveys.delete_spot(m.group(1))
                return self._json({"ok": True})
        raise ApiError(404, "not found")

    # ------------------------------------------------------------ phone

    def _phone_routes(self, method: str, path: str) -> None:
        app = self.server.app
        if method == "GET":
            if (m := re.fullmatch(r"/p/([\w-]+)", path)) and app.phone.check(m.group(1)):
                return self._static("phone.html")
            if (m := re.fullmatch(r"/static/([\w.-]+)", path)) and m.group(1) in PHONE_ASSETS:
                return self._static(m.group(1))
        elif method == "POST" and (m := re.fullmatch(r"/api/p/([\w-]+)/(report|measure)", path)):
            if not app.phone.check(m.group(1)):
                raise ApiError(403, "This pairing link is no longer valid. Scan the QR code again.")
            data = self._body()
            if m.group(2) == "report":
                app.phone.report(data, time.time())
            else:
                app.surveys.measure(str(data.get("label", "")))
            return self._json(app.phone_state())
        raise ApiError(404, "not found")
