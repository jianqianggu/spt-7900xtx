# SPT server connect page

Public instructions for the shared SPT 4.1.6 server: how to point the launcher at it, check for breaking mod-pack updates, and bring an existing profile.

The game install, profiles, certificates, and server configs are not in this repository.

## What is included here

- `index.html` — connect page served by GitHub Pages and by the Tailscale/LAN status service (`:6970`).
- `server-updates.json` — public changelog, breaking-change banner data, mod pack revision tag, and required vs. optional client mod GUID list.
- `scripts/profile_import.py` — profile validation, headless-profile exclusion, and atomic save with automatic `.backups/` on replace.
- `scripts/profile_upload_server.py` — Tailscale/LAN HTTPS service for `/status`, `/manifest`, `/server-updates.json`, `/usernames`, `/import` (blocked during active raids), `/download`, and `/client-mods.zip`.
- `deploy/` — systemd user unit templates.

## Publishing a breaking change

1. Rebuild and upload the updated `spt-7900xtx-client-mods.zip` to the server.
2. Update `server-updates.json` with the new `packVersion`, `serverName`, and a new top entry in `updates` with `"breaking": true` and a clear `action` field.
3. Update `serverName` in `SPT_Data/configs/core.json` to match `serverName` in `server-updates.json`, and update `client.mods.required` / `client.mods.optional` in `fika.jsonc` if GUIDs changed.
4. Copy `index.html` and `server-updates.json` to `~/spt/www/` on the server and push `main` so both GitHub Pages and `:6970` show the new banner immediately.
