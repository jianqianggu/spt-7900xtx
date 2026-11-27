"""Validate and store one SPT profile.

The CLI and the Tailscale upload service both use this module.
A profile can only be added. It cannot take a username that already exists.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-f0-9]{24}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ImportRejected(Exception):
    """The profile failed a check and was not written."""


def parse_profile(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ImportRejected(f"profile size must be 1 byte to {MAX_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImportRejected(f"profile is not UTF-8 JSON ({exc.__class__.__name__})") from exc
    if not isinstance(data, dict):
        raise ImportRejected("profile must be a JSON object")
    return data


def validate(data: dict) -> tuple[str, str]:
    info = data.get("info")
    characters = data.get("characters")
    if not isinstance(info, dict) or not isinstance(characters, dict):
        raise ImportRejected("profile must contain info and characters objects")
    profile_id = info.get("id")
    username = info.get("username")
    if not isinstance(profile_id, str) or not ID_RE.fullmatch(profile_id):
        raise ImportRejected("info.id must be 24 lowercase hex characters")
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        raise ImportRejected("info.username must be 1-32 letters, numbers, underscores, or hyphens")
    scav_id = info.get("scavId")
    if scav_id is not None and (not isinstance(scav_id, str) or not ID_RE.fullmatch(scav_id)):
        raise ImportRejected("info.scavId must be 24 lowercase hex characters when present")
    if profile_id == scav_id:
        raise ImportRejected("info.id and info.scavId must be different")
    return profile_id, username


def _claims(profiles_dir: Path) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for path in profiles_dir.glob("*.json"):
        if path.name.startswith(".") or path.is_symlink() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            info = data.get("info", {})
            claims.append((str(info.get("id", "")), str(info.get("username", ""))))
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ImportRejected(f"existing profile cannot be read safely: {path.name}") from exc
    return claims


def store_profile(data: dict, profiles_dir: Path, replace: bool = False) -> tuple[str, str]:
    if not profiles_dir.is_dir() or profiles_dir.is_symlink():
        raise ImportRejected("profiles directory is missing")
    profile_id, username = validate(data)
    destination = profiles_dir / f"{profile_id}.json"
    if destination.is_symlink():
        raise ImportRejected("refusing to follow a symlink in the profiles directory")

    for existing_id, existing_username in _claims(profiles_dir):
        same_file = existing_id == profile_id
        if existing_username.lower() == username.lower() and not same_file:
            raise ImportRejected("that username is already used by another profile")
        if same_file and not replace:
            raise ImportRejected("this profile is already on the server; log in with your username")
        if same_file and replace and existing_username != username:
            raise ImportRejected("refusing to replace a profile whose username changed")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ImportRejected("profile is too large after normalization")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{profile_id}.", suffix=".tmp", dir=profiles_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(destination, 0o600)
    return profile_id, username
