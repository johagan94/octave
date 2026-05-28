"""Subsonic/OpenSubsonic API endpoint handlers.

Mounted at ``/rest`` in the Octave FastAPI app.  All Subsonic endpoints
follow the pattern ``/rest/{action}.view`` (or ``/rest/{action}``).
Parameters arrive via query string (GET) or form body (POST).

Implemented endpoints (Amperfy MVP set):
  System:      ping, getOpenSubsonicExtensions
  Browsing:    getMusicFolders, getArtists, getArtist, getAlbum, getSong
               getGenres, getMusicDirectory (alias → getArtist/getAlbum)
  Lists:       getAlbumList2, getRandomSongs, getStarred2
               getSimilarSongs2, getTopSongs
  Search:      search3
  Playlists:   getPlaylists, getPlaylist, createPlaylist,
               updatePlaylist, deletePlaylist
  Media:       stream, download, getCoverArt
  Annotation:  star, unstar, setRating, scrobble
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from ...subsonic import auth as _auth
from ...subsonic import mapper as _map
from ...subsonic import response as _resp
from ...subsonic.client import JellyfinClient
from ...web import settings as _settings

log = logging.getLogger(__name__)

router = APIRouter()

# ── dispatch helper ───────────────────────────────────────────────────────────

async def _params(request: Request) -> dict[str, str]:
    """Merge query params + form body into a single flat dict."""
    p = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            p.update({k: v for k, v in form.items()})
        except Exception:
            pass
    return p


def _p(params: dict, key: str, default=None):
    v = params.get(key)
    if v is None or v == "":
        return default
    return v


def _pi(params: dict, key: str, default: int = 0) -> int:
    try:
        return int(params.get(key, default))
    except (TypeError, ValueError):
        return default


def _fmt(params: dict) -> str:
    return params.get("f", "xml").lower()


# ── auth dependency (inline, not FastAPI Depends — keeps response format) ─────

def _check_auth(params: dict) -> tuple[str, str] | None:
    return _auth.verify(
        u=params.get("u"),
        t=params.get("t"),
        s=params.get("s"),
        p=params.get("p"),
    )


def _client(api_key: str, user_id: str) -> JellyfinClient:
    return JellyfinClient(
        base_url=_settings.get_setting("JELLYFIN_URL"),
        api_key=api_key,
        user_id=user_id,
    )


# ── entry point (all .view routes and bare routes) ────────────────────────────

@router.api_route("/{action:path}", methods=["GET", "POST"])
async def dispatch(action: str, request: Request) -> Response:
    # Strip trailing .view and leading slashes
    action = action.removesuffix(".view").strip("/")

    params = await _params(request)
    fmt = _fmt(params)

    # Ping and OpenSubsonic extensions require no auth
    if action == "ping":
        return _resp.ok(fmt)

    if action == "getOpenSubsonicExtensions":
        return _resp.ok(fmt, {
            "openSubsonicExtensions": [
                {"name": "formPost", "versions": [1]},
                {"name": "songLyrics", "versions": [1]},
            ]
        })

    # All other endpoints require auth
    creds = _check_auth(params)
    if creds is None:
        return _resp.err(fmt, _resp.ERR_WRONG_CREDENTIALS, "Wrong username or password.")

    api_key, user_id = creds
    jf = _client(api_key, user_id)

    try:
        return await _handle(action, params, fmt, jf, request)
    except Exception as exc:
        log.exception("[subsonic] unhandled error in %r: %s", action, exc)
        return _resp.err(fmt, _resp.ERR_GENERIC, f"Internal error: {exc}")


async def _handle(action: str, params: dict, fmt: str, jf: JellyfinClient, request) -> Response:  # noqa: C901
    # ── system ──────────────────────────────────────────────────────────────
    if action == "getLicense":
        return _resp.ok(fmt, {"license": {"valid": True, "email": "octave@local", "licenseExpires": "2099-01-01"}})

    # ── browsing ────────────────────────────────────────────────────────────
    if action == "getMusicFolders":
        folders = await jf.get_music_folders()
        return _resp.ok(fmt, {
            "musicFolders": {
                "musicFolder": [{"id": f["Id"], "name": f["Name"]} for f in folders]
            }
        })

    if action == "getArtists":
        data = await jf.get_artists()
        return _resp.ok(fmt, {"artists": _map.artists_index(data.get("Items", []))})

    if action == "getArtist":
        artist_id = _p(params, "id")
        if not artist_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        data = await jf.get_artist(artist_id)
        a = _map.artist(data["item"])
        albums = [_map.album(x) for x in data["albums"]]
        a["albumCount"] = len(albums)  # override with ground truth from actual album list
        a["album"] = albums
        return _resp.ok(fmt, {"artist": a})

    if action == "getAlbum":
        album_id = _p(params, "id")
        if not album_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        data = await jf.get_album(album_id)
        alb = _map.album(data["item"])
        alb["song"] = [_map.song(t) for t in data["tracks"]]
        return _resp.ok(fmt, {"album": alb})

    if action == "getSong":
        song_id = _p(params, "id")
        if not song_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        item = await jf.get_song(song_id)
        return _resp.ok(fmt, {"song": _map.song(item)})

    if action in ("getMusicDirectory", "getIndexes"):
        # Subsonic folder-style browsing — alias to artist or album depending on ID
        item_id = _p(params, "id")
        if not item_id:
            # No ID → return top-level (artists as directory)
            data = await jf.get_artists()
            children = [{"id": a["Id"], "parent": "root", "isDir": True, "title": a["Name"]}
                        for a in data.get("Items", [])]
            return _resp.ok(fmt, {"directory": {"id": "root", "name": "Music", "child": children}})
        # Try album first, fall back to artist
        try:
            data = await jf.get_album(item_id)
            children = [_map.song(t) | {"parent": item_id, "isDir": False}
                        for t in data["tracks"]]
            return _resp.ok(fmt, {
                "directory": {"id": item_id, "name": data["item"].get("Name", ""), "child": children}
            })
        except Exception:
            try:
                data = await jf.get_artist(item_id)
                children = [_map.album(a) | {"parent": item_id, "isDir": True}
                            for a in data["albums"]]
                return _resp.ok(fmt, {
                    "directory": {"id": item_id, "name": data["item"].get("Name", ""), "child": children}
                })
            except Exception:
                return _resp.err(fmt, _resp.ERR_NOT_FOUND, "Directory not found.")

    if action == "getGenres":
        genres = await jf.get_genres()
        return _resp.ok(fmt, {"genres": {"genre": [_map.genre(g) for g in genres]}})

    # ── lists ────────────────────────────────────────────────────────────────
    if action in ("getAlbumList", "getAlbumList2"):
        list_type = _p(params, "type", "alphabeticalByName")
        size = min(_pi(params, "size", 10), 500)
        offset = _pi(params, "offset", 0)
        from_year = _pi(params, "fromYear") or None
        to_year = _pi(params, "toYear") or None
        genre = _p(params, "genre")
        albums = await jf.get_albums(list_type, size, offset, from_year, to_year, genre)
        tag = "albumList2" if action == "getAlbumList2" else "albumList"
        return _resp.ok(fmt, {tag: {"album": [_map.album(a) for a in albums]}})

    if action == "getRandomSongs":
        size = min(_pi(params, "size", 10), 500)
        songs = await jf.get_random_songs(
            size=size,
            genre=_p(params, "genre"),
            from_year=_pi(params, "fromYear") or None,
            to_year=_pi(params, "toYear") or None,
        )
        return _resp.ok(fmt, {"randomSongs": {"song": [_map.song(s) for s in songs]}})

    if action in ("getStarred", "getStarred2"):
        data = await jf.get_starred()
        tag = "starred2" if action == "getStarred2" else "starred"
        return _resp.ok(fmt, {
            tag: {
                "artist": [_map.artist(a) for a in data["artists"]],
                "album": [_map.album(a) for a in data["albums"]],
                "song": [_map.song(s) for s in data["songs"]],
            }
        })

    if action == "getSimilarSongs2":
        item_id = _p(params, "id")
        if not item_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        count = min(_pi(params, "count", 50), 200)
        songs = await jf.get_similar_songs(item_id, count)
        return _resp.ok(fmt, {"similarSongs2": {"song": [_map.song(s) for s in songs]}})

    if action == "getTopSongs":
        artist_id = _p(params, "id") or _p(params, "artist")
        if not artist_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        count = min(_pi(params, "count", 50), 200)
        songs = await jf.get_top_songs(artist_id, count)
        return _resp.ok(fmt, {"topSongs": {"song": [_map.song(s) for s in songs]}})

    if action == "getNowPlaying":
        # Jellyfin Sessions endpoint — return empty for now
        return _resp.ok(fmt, {"nowPlaying": {}})

    # ── search ───────────────────────────────────────────────────────────────
    if action in ("search2", "search3"):
        query = _p(params, "query", "")
        results = await jf.search(
            query=query,
            artist_count=min(_pi(params, "artistCount", 20), 500),
            artist_offset=_pi(params, "artistOffset", 0),
            album_count=min(_pi(params, "albumCount", 20), 500),
            album_offset=_pi(params, "albumOffset", 0),
            song_count=min(_pi(params, "songCount", 20), 500),
            song_offset=_pi(params, "songOffset", 0),
        )
        tag = "searchResult3" if action == "search3" else "searchResult2"
        return _resp.ok(fmt, {
            tag: {
                "artist": [_map.artist(a) for a in results["artists"]],
                "album": [_map.album(a) for a in results["albums"]],
                "song": [_map.song(s) for s in results["songs"]],
            }
        })

    # ── playlists ────────────────────────────────────────────────────────────
    if action == "getPlaylists":
        pls = await jf.get_playlists()
        return _resp.ok(fmt, {"playlists": {"playlist": [_map.playlist(p) for p in pls]}})

    if action == "getPlaylist":
        pl_id = _p(params, "id")
        if not pl_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        data = await jf.get_playlist(pl_id)
        return _resp.ok(fmt, {"playlist": _map.playlist(data["item"], data["tracks"])})

    if action == "createPlaylist":
        name = _p(params, "name") or _p(params, "playlistId")
        song_ids = [v for k, v in params.items() if k == "songId"]
        if not name:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'name' is missing.")
        user_tok = await _get_user_token()
        result = await jf.create_playlist(name, song_ids, user_token=user_tok)
        pl_id = result.get("Id")
        if pl_id:
            data = await jf.get_playlist(pl_id)
            return _resp.ok(fmt, {"playlist": _map.playlist(data["item"], data["tracks"])})
        return _resp.ok(fmt)

    if action == "updatePlaylist":
        pl_id = _p(params, "playlistId")
        if not pl_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'playlistId' is missing.")
        add_ids = [v for k, v in params.items() if k == "songIdToAdd"]
        remove_indices = [int(v) for k, v in params.items() if k == "songIndexToRemove"]
        name = _p(params, "name")
        user_tok = await _get_user_token()
        await jf.update_playlist(pl_id, name=name, add_ids=add_ids or None,
                                  remove_indices=remove_indices or None, user_token=user_tok)
        return _resp.ok(fmt)

    if action == "deletePlaylist":
        pl_id = _p(params, "id")
        if not pl_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        user_tok = await _get_user_token()
        await jf.delete_playlist(pl_id, user_token=user_tok)
        return _resp.ok(fmt)

    # ── media retrieval ──────────────────────────────────────────────────────
    if action in ("stream", "download"):
        item_id = _p(params, "id")
        if not item_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")

        range_hdr = request.headers.get("Range")
        max_bit_rate = _pi(params, "maxBitRate") or None
        audio_fmt = _p(params, "format")

        status, headers, body = await jf.stream_audio(
            item_id, range_header=range_hdr, max_bit_rate=max_bit_rate, fmt=audio_fmt
        )
        from fastapi.responses import StreamingResponse
        return StreamingResponse(body, status_code=status, headers=headers,
                                  media_type=headers.get("Content-Type", "audio/mpeg"))

    if action == "getCoverArt":
        item_id = _p(params, "id")
        if not item_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        # Strip ar- or al- prefixes some clients may send
        item_id = item_id.removeprefix("ar-").removeprefix("al-")
        size = _pi(params, "size") or None
        jf_url = _settings.get_setting("JELLYFIN_URL").rstrip("/")
        params_str = f"?fillWidth={size}&fillHeight={size}" if size else ""
        img_url = f"{jf_url}/Items/{item_id}/Images/Primary{params_str}"
        # Proxy the image
        import httpx as _httpx
        auth_hdr = f'MediaBrowser Token="{_settings.get_setting("JELLYFIN_API_KEY")}", Client="Octave-Subsonic", DeviceId="octave-subsonic-01", Device="Octave", Version="1.0.0"'
        try:
            async with _httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(img_url, headers={"Authorization": auth_hdr})
                if r.status_code == 200:
                    return Response(content=r.content,
                                    media_type=r.headers.get("content-type", "image/jpeg"))
        except Exception as exc:
            log.debug("[subsonic] getCoverArt failed for %s: %s", item_id, exc)
        return _resp.err(fmt, _resp.ERR_NOT_FOUND, "Cover art not found.")

    # ── annotation ───────────────────────────────────────────────────────────
    if action == "star":
        ids = [v for k, v in params.items() if k in ("id", "albumId", "artistId")]
        for i in ids:
            await jf.star(i)
        return _resp.ok(fmt)

    if action == "unstar":
        ids = [v for k, v in params.items() if k in ("id", "albumId", "artistId")]
        for i in ids:
            await jf.unstar(i)
        return _resp.ok(fmt)

    if action == "setRating":
        item_id = _p(params, "id")
        rating = _pi(params, "rating", 0)
        if not item_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        await jf.set_rating(item_id, rating)
        return _resp.ok(fmt)

    if action == "scrobble":
        item_id = _p(params, "id")
        if not item_id:
            return _resp.err(fmt, _resp.ERR_MISSING_PARAM, "Required parameter 'id' is missing.")
        submission = _p(params, "submission", "true").lower() != "false"
        time_ms = _pi(params, "time") or None
        await jf.scrobble(item_id, submission=submission, position_ms=time_ms)
        return _resp.ok(fmt)

    # ── user (stub — single user) ────────────────────────────────────────────
    if action in ("getUser", "getUsers"):
        username = (
            _settings.get_setting("SUBSONIC_USERNAME")
            or _settings.get_setting("AUTH_USERNAME")
            or "octave"
        )
        user_obj = {
            "username": username,
            "email": "",
            "scrobblingEnabled": True,
            "adminRole": True,
            "settingsRole": True,
            "downloadRole": True,
            "uploadRole": False,
            "playlistRole": True,
            "coverArtRole": True,
            "commentRole": False,
            "podcastRole": False,
            "streamRole": True,
            "jukeboxRole": False,
            "shareRole": False,
            "videoConversionRole": False,
        }
        if action == "getUsers":
            return _resp.ok(fmt, {"users": {"user": [user_obj]}})
        return _resp.ok(fmt, {"user": user_obj})

    # ── library scan status (stub) ────────────────────────────────────────────
    if action == "getScanStatus":
        return _resp.ok(fmt, {"scanStatus": {"scanning": False, "count": 0}})

    # ── not implemented ───────────────────────────────────────────────────────
    log.debug("[subsonic] unimplemented action: %r", action)
    return _resp.err(fmt, _resp.ERR_GENERIC, f"Action '{action}' is not supported.")


# ── user token helper ─────────────────────────────────────────────────────────

async def _get_user_token() -> str | None:
    """Get a Jellyfin user token for write operations (playlist CRUD)."""
    username = (
        _settings.get_setting("SUBSONIC_USERNAME")
        or _settings.get_setting("AUTH_USERNAME")
        or "octave"
    )
    password = _settings.get_setting("SUBSONIC_PASSWORD")
    if not password:
        return None

    jf_url = _settings.get_setting("JELLYFIN_URL")
    api_key = _settings.get_setting("JELLYFIN_API_KEY")
    user_id = _settings.get_setting("JELLYFIN_USER_ID")
    jf = JellyfinClient(base_url=jf_url, api_key=api_key, user_id=user_id)

    # We use the Jellyfin username (which may differ from Subsonic username)
    jf_username = _settings.get_setting("JELLYFIN_USERNAME") or username
    return await jf.get_user_token(jf_username, password)
