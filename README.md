# SPT server connect page

Public instructions for the shared SPT 4.1.6 server: how to point the launcher at it, and how to bring an existing profile.

The game install, profiles, certificates, and server configs are not in this repository. The connect page sends a profile to the Tailscale import service (`scripts/profile_upload_server.py`). That service checks the file, refuses username collisions, and does not accept a replacement for a profile already on the server. It also reports whether SPT is up and returns the latest saved copy of one username.

GitHub Pages serves `index.html`.
