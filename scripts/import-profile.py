#!/usr/bin/env python3
"""Import one SPT profile from a file on the server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from profile_import import ImportRejected, parse_profile, store_profile


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
    path = args.profile.resolve()
    if not path.is_file() or path.is_symlink():
        print("rejected: profile path must be a regular file", file=sys.stderr)
        raise SystemExit(1)
    try:
        profile_id, username = store_profile(
            parse_profile(path.read_bytes()),
            args.profiles_dir.resolve(),
            replace=args.replace,
        )
    except ImportRejected as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"imported profile {profile_id} for username {username}")


if __name__ == "__main__":
    main()
