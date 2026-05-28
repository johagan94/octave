"""Subsonic authentication helpers.

Subsonic clients authenticate every request with one of two schemes:

  MD5 token (preferred):
    u=username  t=md5(password+salt)  s=salt

  Legacy plain/hex:
    u=username  p=password            (or p=enc:<hex>)

Because Jellyfin stores passwords as PBKDF2 hashes we cannot verify
Subsonic MD5 tokens against Jellyfin's password store.  Instead we keep
a separate plaintext ``SUBSONIC_PASSWORD`` in Octave's settings (reversibly
stored, never sent to clients).  For single-user setups this is the only
account; multi-user support can extend ``_get_user_creds`` later.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from ..web import settings as _settings

log = logging.getLogger(__name__)


def _get_user_creds(username: str) -> tuple[str, str, str] | None:
    """Return (subsonic_password, jellyfin_api_key, jellyfin_user_id) or None."""
    configured_user = (
        _settings.get_setting("SUBSONIC_USERNAME")
        or _settings.get_setting("AUTH_USERNAME")
        or "octave"
    ).strip().lower()

    if username.strip().lower() != configured_user:
        return None

    password = _settings.get_setting("SUBSONIC_PASSWORD")
    api_key = _settings.get_setting("JELLYFIN_API_KEY")
    user_id = _settings.get_setting("JELLYFIN_USER_ID")

    if not password or not api_key or not user_id:
        return None

    return password, api_key, user_id


def verify(
    u: str | None,
    t: str | None,
    s: str | None,
    p: str | None,
) -> tuple[str, str] | None:
    """Verify Subsonic credentials.

    Returns ``(jellyfin_api_key, jellyfin_user_id)`` on success, ``None`` on failure.
    """
    if not u:
        return None

    creds = _get_user_creds(u)
    if creds is None:
        log.debug("[subsonic] unknown user: %r", u)
        return None

    stored_password, api_key, user_id = creds

    if t and s:
        # MD5 token mode
        expected = hashlib.md5((stored_password + s).encode()).hexdigest()
        if not hmac.compare_digest(t.lower(), expected.lower()):
            log.debug("[subsonic] MD5 token mismatch for %r", u)
            return None
    elif p:
        # Legacy mode — may be hex-encoded with enc: prefix
        if p.startswith("enc:"):
            try:
                candidate = bytes.fromhex(p[4:]).decode("utf-8")
            except Exception:
                return None
        else:
            candidate = p
        if not hmac.compare_digest(candidate, stored_password):
            log.debug("[subsonic] password mismatch for %r", u)
            return None
    else:
        return None

    return api_key, user_id
