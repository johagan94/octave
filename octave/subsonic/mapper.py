"""Jellyfin item → Subsonic entity conversion.

All functions return plain dicts that feed directly into the response helpers.
None values are omitted by the XML/JSON serialisers.
"""

from __future__ import annotations

import unicodedata


def _clean(v) -> str | None:
    """Return stripped string or None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _ticks_to_sec(ticks: int | None) -> int:
    if not ticks:
        return 0
    return ticks // 10_000_000


def _starred(user_data: dict) -> str | None:
    """Return ISO timestamp if item is starred, else None."""
    if user_data.get("IsFavorite"):
        return user_data.get("LastPlayedDate") or "1970-01-01T00:00:00"
    return None


def _rating(user_data: dict) -> int | None:
    r = user_data.get("Rating")
    if r is None:
        return None
    return max(1, min(5, round(r / 2)))


def _primary_image(item: dict) -> str | None:
    if item.get("ImageTags", {}).get("Primary"):
        return item["Id"]
    return None


def _artist_ids(item: dict) -> str | None:
    ids = [a["Id"] for a in item.get("ArtistItems", []) if a.get("Id")]
    return ids[0] if ids else None


def _artist_name(item: dict) -> str:
    return (
        item.get("AlbumArtist")
        or (item.get("ArtistItems") or [{}])[0].get("Name")
        or ""
    )


# ── entity mappers ────────────────────────────────────────────────────────────

def artist(item: dict) -> dict:
    ud = item.get("UserData") or {}
    # _AlbumCount is injected by JellyfinClient.get_artists() via a parallel batch query.
    # For single-artist fetches (getArtist), ChildCount is populated by Jellyfin.
    album_count = (
        item.get("_AlbumCount")
        or item.get("ChildCount")
        or item.get("AlbumCount")
    )
    return {
        "id": item["Id"],
        "name": item.get("Name", ""),
        "coverArt": _primary_image(item),
        "albumCount": album_count,
        "starred": _starred(ud),
        "userRating": _rating(ud),
    }


def album(item: dict) -> dict:
    ud = item.get("UserData") or {}
    artist_id = _artist_ids(item) or item.get("AlbumArtistId")
    return {
        "id": item["Id"],
        "name": item.get("Name", ""),
        "artist": _artist_name(item),
        "artistId": artist_id,
        "coverArt": _primary_image(item),
        "songCount": item.get("ChildCount") or 0,
        "duration": _ticks_to_sec(item.get("RunTimeTicks")),
        "playCount": ud.get("PlayCount") or 0,
        "created": item.get("DateCreated"),
        "year": item.get("ProductionYear"),
        "genre": (item.get("Genres") or [None])[0],
        "starred": _starred(ud),
        "userRating": _rating(ud),
    }


def song(item: dict) -> dict:
    ud = item.get("UserData") or {}
    sources = item.get("MediaSources") or [{}]
    ms = sources[0] if sources else {}
    streams = ms.get("MediaStreams") or []
    audio_stream = next((s for s in streams if s.get("Type") == "Audio"), {})
    container = ms.get("Container") or ""
    bit_rate = (audio_stream.get("BitRate") or 0) // 1000

    return {
        "id": item["Id"],
        "parent": item.get("AlbumId") or "",
        "isDir": False,
        "title": item.get("Name", ""),
        "album": item.get("Album"),
        "artist": _artist_name(item),
        "track": item.get("IndexNumber"),
        "year": item.get("ProductionYear"),
        "genre": (item.get("Genres") or [None])[0],
        "coverArt": item.get("AlbumId") or _primary_image(item),
        "size": ms.get("Size"),
        "contentType": _mime(container),
        "suffix": container or None,
        "duration": _ticks_to_sec(item.get("RunTimeTicks")),
        "bitRate": bit_rate or None,
        "samplingRate": audio_stream.get("SampleRate"),
        "channelCount": audio_stream.get("Channels"),
        "bitDepth": audio_stream.get("BitDepth"),
        "path": _fake_path(item),
        "discNumber": item.get("ParentIndexNumber"),
        "created": item.get("DateCreated"),
        "albumId": item.get("AlbumId"),
        "artistId": _artist_ids(item),
        "type": "music",
        "mediaType": "song",
        "playCount": ud.get("PlayCount") or 0,
        "played": ud.get("LastPlayedDate"),
        "starred": _starred(ud),
        "userRating": _rating(ud),
    }


def playlist(item: dict, tracks: list[dict] | None = None) -> dict:
    ud = item.get("UserData") or {}
    p: dict = {
        "id": item["Id"],
        "name": item.get("Name", ""),
        "comment": item.get("Overview"),
        "owner": "admin",
        "public": False,
        "songCount": item.get("ChildCount") or (len(tracks) if tracks else 0),
        "duration": _ticks_to_sec(item.get("RunTimeTicks")),
        "created": item.get("DateCreated"),
        "changed": ud.get("LastPlayedDate") or item.get("DateCreated"),
        "coverArt": _primary_image(item),
    }
    if tracks is not None:
        p["entry"] = [song(t) for t in tracks]
    return p


def genre(item: dict) -> dict:
    counts = item.get("ChildCount") or 0
    return {
        "songCount": counts,
        "albumCount": item.get("AlbumCount") or 0,
        "value": item.get("Name", ""),
    }


# ── artists index (grouped by first letter) ───────────────────────────────────

def artists_index(items: list[dict]) -> dict:
    """Build the getArtists index structure grouped by first letter."""
    buckets: dict[str, list[dict]] = {}
    for item in items:
        name = item.get("Name") or "?"
        letter = _index_letter(name)
        buckets.setdefault(letter, []).append(artist(item))

    index = [
        {"name": letter, "artist": entries}
        for letter, entries in sorted(buckets.items())
    ]
    return {
        "ignoredArticles": "The An A Die Das Ein Les Le La",
        "index": index,
    }


def _index_letter(name: str) -> str:
    if not name:
        return "#"
    # Strip leading "The ", "A ", "An " for sorting
    for prefix in ("The ", "A ", "An "):
        if name.upper().startswith(prefix.upper()):
            name = name[len(prefix):]
            break
    ch = name[0].upper()
    # Normalise accented characters to their base letter
    ch = unicodedata.normalize("NFD", ch)[0]
    return ch if ch.isalpha() else "#"


# ── helpers ───────────────────────────────────────────────────────────────────

_MIME = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
    "m4b": "audio/mp4",
    "alac": "audio/mp4",
    "wav": "audio/wav",
    "wma": "audio/x-ms-wma",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}


def _mime(container: str) -> str:
    return _MIME.get(container.lower(), f"audio/{container.lower()}" if container else "audio/mpeg")


def _fake_path(item: dict) -> str:
    """Construct a plausible relative path for clients that display it."""
    artist = _artist_name(item) or "Unknown Artist"
    album = item.get("Album") or "Unknown Album"
    title = item.get("Name") or "Unknown"
    return f"{artist}/{album}/{title}"
