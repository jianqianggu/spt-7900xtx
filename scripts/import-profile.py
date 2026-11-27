#!/usr/bin/env python3
"""Import one SPT profile onto this server.

Run this on the server, as the account that owns the SPT files.
Players do not get a copy of the profiles directory and this script
does not listen on the network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-f0-9]{24}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def fail(message: str) -> None:
    print(f"rejected: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_profile(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        fail("profile path must be a regular file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_BYTES:
        fail(f"profile size must be 1 byte to {MAX_BYTES} bytes")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"profile is not UTF-8 JSON ({exc.__class__.__name__})")
    if not isinstance(data, dict):
        fail("profile must be a JSON object")
    return data


def validate(data: dict) -> tuple[str, str]:
    info = data.get("info")
    characters = data.get("characters")
    if not isinstance(info, dict) or not isinstance(characters, dict):
        fail("profile must contain info and characters objects")
    profile_id = info.get("id")
    username = info.get("username")
    if not isinstance(profile_id, str) or not ID_RE.fullmatch(profile_id):
        fail("info.id must be 24 lowercase hex characters")
    if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
        fail("info.username must be 1-32 letters, numbers, underscores, or hyphens")
    scav_id = info.get("scavId")
    if scav_id is not None and (not isinstance(scav_id, str) or not ID_RE.fullmatch(scav_id)):
        fail("info.scavId must be 24 lowercase hex characters when present")
    if profile_id == scav_id:
        fail("info.id and info.scavId must be different")
    return profile_id, username


def existing_claims(profiles_dir: Path) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for path in profiles_dir.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            info = data.get("info", {})
            claims.append((str(info.get("id", "")), str(info.get("username", ""))))
        except (UnicodeError, json.JSONDecodeError, OSError):
            fail(f"existing profile cannot be read safely: {path.name}")
    return claims


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one SPT profile JSON file")
    parser.add_argument("profile", type=Path, help="Path to the player's own profile JSON")
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "user" / "profiles",
        help="Server user/profiles directory",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing file only when both the id and username match",
    )
    args = parser.parse_args()

    profiles_dir = args.profiles_dir.resolve()
    if not profiles_dir.is_dir() or profiles_dir.is_symlink():
        fail("profiles directory is missing")

    data = load_profile(args.profile.resolve())
    profile_id, username = validate(data)
    destination = profiles_dir / f"{profile_id}.json"
    if destination.is_symlink():
        fail("refusing to follow a symlink in the profiles directory")

    for existing_id, existing_username in existing_claims(profiles_dir):
        same_file = existing_id == profile_id
        if existing_username.lower() == username.lower() and not same_file:
            fail("that username is already used by another profile")
        if same_file and not args.replace:
            fail("that profile id is already on the server; the admin must pass --replace")
        if same_file and args.replace and existing_username != username:
            fail("refusing to replace a profile whose username changed")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        fail("profile is too large after normalization")

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
    print(f"imported profile {profile_id} for username {username}")


if __name__ == "__main__":
    main()
