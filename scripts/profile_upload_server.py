#!/usr/bin/env python3
"""Accept one SPT profile upload and reload the game server.

The listener binds to the Tailscale address only. Browsers must come from the
public connect page and send the SPTProfileImport header, which blocks another
website from submitting a profile through a player's browser.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from profile_import import ImportRejected, parse_profile, store_profile

BIND_HOST = os.environ.get("SPT_BIND_HOST", "100.64.91.97")
PORT = int(os.environ.get("SPT_IMPORT_PORT", "6970"))
PROFILES_DIR = Path(os.environ.get("SPT_PROFILES_DIR", "/home/jianqianggu/spt/user/profiles"))
CERT_FILE = Path(os.environ.get("SPT_TLS_CERT", "/home/jianqianggu/spt/certs/import.crt"))
KEY_FILE = Path(os.environ.get("SPT_TLS_KEY", "/home/jianqianggu/spt/certs/import.key"))
ALLOWED_ORIGIN = os.environ.get("SPT_ALLOWED_ORIGIN", "https://jianqianggu.github.io")
IMPORT_HEADER = "SPTProfileImport"
MAX_BODY = 8 * 1024 * 1024
WINDOW_SECONDS = 600
MAX_ATTEMPTS = 20
RELOAD_GAP_SECONDS = 45
SKIP_RELOAD = os.environ.get("SPT_SKIP_RELOAD") == "1"

_lock = threading.Lock()
_attempts: dict[str, list[float]] = {}
_last_reload = -RELOAD_GAP_SECONDS


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    return handler.client_address[0]


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    recent = [stamp for stamp in _attempts.get(ip, []) if now - stamp < WINDOW_SECONDS]
    _attempts[ip] = recent
    if len(recent) >= MAX_ATTEMPTS:
        return True
    recent.append(now)
    return False


def _reload_server() -> None:
    global _last_reload
    if SKIP_RELOAD:
        _last_reload = time.monotonic()
        return
    subprocess.run(
        ["systemctl", "--user", "restart", "spt-server.service"],
        check=True,
        timeout=90,
    )
    _last_reload = time.monotonic()


class ImportHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SPTProfileImport"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        return self.headers.get("Origin") == ALLOWED_ORIGIN

    def do_OPTIONS(self) -> None:
        if not self._origin_ok():
            self._json(403, {"ok": False, "error": "this upload only accepts the connect page"})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            self._json(404, {"ok": False, "error": "not found"})
            return
        self._json(200, {"ok": True})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/import":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._origin_ok() or self.headers.get("X-Requested-With") != IMPORT_HEADER:
            self._json(403, {"ok": False, "error": "this upload only accepts the connect page"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"ok": False, "error": "send the profile JSON file"})
            return
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            self._json(411, {"ok": False, "error": "profile size is missing"})
            return
        size = int(length)
        if size <= 0 or size > MAX_BODY:
            self._json(413, {"ok": False, "error": "profile must be 8 MB or smaller"})
            return
        raw = self.rfile.read(size)
        if len(raw) != size:
            self._json(400, {"ok": False, "error": "profile upload was incomplete"})
            return

        ip = _client_ip(self)
        with _lock:
            if _rate_limited(ip):
                self._json(429, {"ok": False, "error": "too many imports from this network; wait and try again"})
                return
            if time.monotonic() - _last_reload < RELOAD_GAP_SECONDS:
                self._json(429, {"ok": False, "error": "the server is still reloading; wait a minute and try again"})
                return
            try:
                _profile_id, username = store_profile(parse_profile(raw), PROFILES_DIR, replace=False)
                _reload_server()
            except ImportRejected as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            except subprocess.SubprocessError:
                print(f"profile stored for {username} but server reload failed", flush=True)
                self._json(500, {"ok": False, "error": "profile was saved, but the server did not reload"})
                return
        print(f"imported username {username} from {ip}", flush=True)
        self._json(
            200,
            {
                "ok": True,
                "username": username,
                "message": "Profile imported. The server is reloading. Wait about a minute, then log in with this username.",
            },
        )


def main() -> None:
    if not BIND_HOST.startswith("100."):
        raise SystemExit("refusing to listen outside the Tailscale address range")
    if not CERT_FILE.is_file() or not KEY_FILE.is_file():
        raise SystemExit("import certificate is missing")
    server = ThreadingHTTPServer((BIND_HOST, PORT), ImportHandler)
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"profile import listening on https://{BIND_HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
