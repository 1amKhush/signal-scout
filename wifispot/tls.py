"""Self-signed certificate for the phone page.

Browsers only expose geolocation to secure contexts, so the phone page must be
served over HTTPS. There is no public name for a laptop on a LAN, so the phone
shows a one-time certificate warning that the user has to accept.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ensure_cert(directory: Path, ip: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cert, key, stamp = directory / "cert.pem", directory / "key.pem", directory / "ip.txt"
    if cert.exists() and key.exists() and stamp.exists() and stamp.read_text() == ip:
        return cert, key
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-days", "825", "-subj", "/CN=wifispot",
         "-addext", f"subjectAltName=IP:{ip}", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    os.chmod(key, 0o600)
    stamp.write_text(ip)
    return cert, key
