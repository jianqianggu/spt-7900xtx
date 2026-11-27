#!/usr/bin/env python3
"""Accept one SPT profile upload, report status, and serve the client mod pack.

The listener binds to the Tailscale address only. Profile import, status, and
profile download must come from the public connect page and send the
SPTProfileImport header, which blocks another website from using a player's
browser. The mod pack is a direct download on that same Tailscale address.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from profile_import import (
    ImportRejected,
    list_usernames,
    parse_profile,
    read_profile_for_username,
    store_profile,
)

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
DEPS_FILE = Path(os.environ.get("SPT_DEPS_FILE", "/home/jianqianggu/spt/SPT.Server.deps.json"))
SPT_PORT = int(os.environ.get("SPT_GAME_PORT", "6969"))
VERSION_RE = re.compile(r"SPT\.Server/(\d+\.\d+\.\d+)")
MOD_PACK = Path(os.environ.get("SPT_CLIENT_MOD_PACK", "/home/jianqianggu/spt/client-mods/spt-7900xtx-client-mods.zip"))
MOD_PACK_NAME = "spt-7900xtx-client-mods.zip"
ACTIVITIES = {
    0: "In the menu",
    1: "In a raid",
    2: "In the stash",
    3: "In the hideout",
    4: "On the flea",
    "IN_MENU": "In the menu",
    "IN_RAID": "In a raid",
    "IN_STASH": "In the stash",
    "IN_HIDEOUT": "In the hideout",
    "IN_FLEA": "On the flea",
}
LOCATIONS = {
    "bigmap": "Customs",
    "factory4_day": "Factory",
    "factory4_night": "Factory",
    "interchange": "Interchange",
    "laboratory": "The Lab",
    "labyrinth": "The Labyrinth",
    "lighthouse": "Lighthouse",
    "rezervbase": "Reserve",
    "sandbox": "Ground Zero",
    "sandbox_high": "Ground Zero",
    "shoreline": "Shoreline",
    "tarkovstreets": "Streets",
    "woods": "Woods",
}

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


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 32 or any(ord(char) < 32 for char in name):
        return None
    return name


def _player_view(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = _clean_name(entry.get("nickname"))
    if name is None:
        return None
    level = entry.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or level < 0 or level > 99:
        level = None
    activity = ACTIVITIES.get(entry.get("activity"), "Online")
    raid = entry.get("raidInformation")
    place = None
    if isinstance(raid, dict):
        place = LOCATIONS.get(raid.get("location"))
        if activity == "In a raid" and raid.get("started") is False:
            activity = "In a raid lobby"
    view = {"nickname": name, "activity": activity}
    if level is not None:
        view["level"] = level
    if place and activity.startswith("In a raid"):
        view["place"] = place
    return view


def _online_players() -> list | None:
    context = ssl._create_unverified_context()
    request = urllib.request.Request(
        f"https://127.0.0.1:{SPT_PORT}/fika/presence/get",
        headers={"responsecompressed": "0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        return None
    if isinstance(payload, dict):
        entries = list(payload.values())
    elif isinstance(payload, list):
        entries = payload
    else:
        return None
    players = []
    for entry in entries[:32]:
        view = _player_view(entry)
        if view is not None:
            players.append(view)
    players.sort(key=lambda item: item["nickname"].casefold())
    return players


def _server_status() -> dict:
    version = "unknown"
    try:
        version_match = VERSION_RE.search(DEPS_FILE.read_text(encoding="utf-8"))
        if version_match:
            version = version_match.group(1)
    except OSError:
        version = "unknown"
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", SPT_PORT), timeout=2):
            listening = True
    except OSError:
        listening = False
    result = {"ok": True, "spt": "up" if listening else "down", "version": version, "players": []}
    if not listening:
        result["playersAvailable"] = False
        return result
    players = _online_players()
    result["playersAvailable"] = players is not None
    result["players"] = players or []
    return result


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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With, X-Replace-Profile")
        self.send_header("Access-Control-Allow-Private-Network", "true")
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

    def _page_request(self) -> bool:
        return self._origin_ok() and self.headers.get("X-Requested-With") == IMPORT_HEADER

    def _read_body(self, limit: int) -> bytes | None:
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            self._json(411, {"ok": False, "error": "request size is missing"})
            return None
        size = int(length)
        if size <= 0 or size > limit:
            self._json(413, {"ok": False, "error": "request is too large"})
            return None
        raw = self.rfile.read(size)
        if len(raw) != size:
            self._json(400, {"ok": False, "error": "request was incomplete"})
            return None
        return raw

    def _download(self) -> None:
        if not self._page_request():
            self._json(403, {"ok": False, "error": "this download only accepts the connect page"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"ok": False, "error": "send the username as JSON"})
            return
        raw = self._read_body(4096)
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "username request is not JSON"})
            return
        username = payload.get("username") if isinstance(payload, dict) else None
        with _lock:
            if _rate_limited(_client_ip(self)):
                self._json(429, {"ok": False, "error": "too many requests from this network; wait and try again"})
                return
            try:
                profile_id, body = read_profile_for_username(username, PROFILES_DIR)
            except ImportRejected as exc:
                status = 404 if str(exc) == "no profile with that username" else 400
                self._json(status, {"ok": False, "error": str(exc)})
                return
        filename = f"{profile_id}.json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        print("downloaded one profile", flush=True)

    def _send_mod_pack(self) -> None:
        pack = MOD_PACK
        if not pack.is_file() or pack.is_symlink():
            self._json(404, {"ok": False, "error": "mod pack is not available"})
            return
        size = pack.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{MOD_PACK_NAME}"')
        self._cors()
        self.end_headers()
        try:
            with pack.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        print("sent client mod pack", flush=True)

    def _usernames(self) -> None:
        if not self._page_request():
            self._json(403, {"ok": False, "error": "this page only accepts the connect page"})
            return
        try:
            names = list_usernames(PROFILES_DIR)
        except ImportRejected as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, {"ok": True, "usernames": names})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/client-mods.zip":
            self._send_mod_pack()
            return
        if path == "/usernames":
            self._usernames()
            return
        if path != "/status":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._page_request():
            self._json(403, {"ok": False, "error": "this page only accepts the connect page"})
            return
        self._json(200, _server_status())

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/download":
            self._download()
            return
        if path != "/import":
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
            replace = self.headers.get("X-Replace-Profile") == "yes"
            try:
                _profile_id, username = store_profile(parse_profile(raw), PROFILES_DIR, replace=replace)
                _reload_server()
            except ImportRejected as exc:
                message = str(exc)
                if message == "this profile is already on the server":
                    self._json(409, {"ok": False, "error": message, "canReplace": True})
                    return
                self._json(400, {"ok": False, "error": message})
                return
            except subprocess.SubprocessError:
                print(f"profile stored for {username} but server reload failed", flush=True)
                self._json(500, {"ok": False, "error": "profile was saved, but the server did not reload"})
                return
        print(f"{'replaced' if replace else 'imported'} username {username} from {ip}", flush=True)
        self._json(
            200,
            {
                "ok": True,
                "username": username,
                "replaced": replace,
                "message": (
                    "Replaced the existing profile. The server is reloading. Wait about a minute, then log in with this username."
                    if replace
                    else "Profile imported. The server is reloading. Wait about a minute, then log in with this username."
                ),
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
