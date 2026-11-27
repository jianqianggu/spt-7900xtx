# SPT server connect page

Public instructions for the shared SPT 4.1.6 server: how to point the launcher at it, and how to bring an existing profile.

The game install, profiles, certificates, and server configs are not in this repository. The connect page sends a profile to the Tailscale import service (`scripts/profile_upload_server.py`). That service checks the file and refuses a username that already belongs to a different profile. Replacing the same profile file requires confirmation from the connect page. The download field can list the usernames already on the server. It also reports whether SPT is up, lists who is connected right now, returns the latest saved copy of one username, and serves the compiled client mod pack to Tailscale members. The pack itself is not stored in this repository.

GitHub Pages serves `index.html`.
