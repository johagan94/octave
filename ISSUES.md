# Octave Release Notes

## Public Readiness Checklist

- Docker Compose uses the default project network so a fresh checkout can start without homelab-specific networks.
- Spotify auth uses PKCE and stores the refresh token in `data/.spotify_pkce_token` with owner-only permissions where supported.
- Public users bring their own Spotify app Client ID because Spotify development-mode apps are allowlisted.
- Optional HTTP Basic auth is controlled by `AUTH_USERNAME` and `AUTH_PASSWORD`; leaving `AUTH_PASSWORD` empty keeps LAN-trust mode.
- UI-managed credentials are saved in `data/settings.json`; env vars still take priority for operators who prefer `.env`.
- Playlist discovery supports `SYNC_ALL_PLAYLISTS` and marks discovered rows separately from manually configured rows.
- Missing-track CSV downloads escape spreadsheet-formula cells before export.

## Known Public Caveats

- A zero-developer-account Spotify setup is not the default public path because extended quota access is organization-oriented.
- Exposing Octave outside a trusted LAN requires setting `AUTH_PASSWORD` and putting it behind TLS.
- Service-name URLs such as `http://jellyfin:8096` only resolve when Octave is attached to the same Docker network as Jellyfin/Lidarr.
