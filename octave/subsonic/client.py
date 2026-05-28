"""Async Jellyfin REST client for the Subsonic translation layer.

Uses httpx with connection pooling.  All methods are coroutines.
Playlist writes use a cached user-scoped token because Jellyfin 10.11+
rejects playlist CRUD when authenticated with a plain API key (bug #15600).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

import httpx

from ..web import settings as _settings

log = logging.getLogger(__name__)

_AUTH_HDR = (
    'MediaBrowser Token="{token}", Client="Octave-Subsonic", '
    'DeviceId="octave-subsonic-01", Device="Octave", Version="1.0.0"'
)

# Cached user token for playlist writes: (token, expires_at)
_user_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()
_TOKEN_TTL = 3600 * 6  # 6 hours


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, user_id: str):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self._api_key = api_key
        self._headers = {"Authorization": _AUTH_HDR.format(token=api_key)}

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_settings(cls) -> "JellyfinClient":
        return cls(
            base_url=_settings.get_setting("JELLYFIN_URL"),
            api_key=_settings.get_setting("JELLYFIN_API_KEY"),
            user_id=_settings.get_setting("JELLYFIN_USER_ID"),
        )

    # ── low-level ─────────────────────────────────────────────────────────────

    async def get(self, path: str, **params: Any) -> dict:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(url, params=params, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def post(self, path: str, json: dict | None = None, user_token: str | None = None) -> dict | None:
        url = f"{self.base_url}{path}"
        headers = dict(self._headers)
        if user_token:
            headers["Authorization"] = _AUTH_HDR.format(token=user_token)
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(url, json=json or {}, headers=headers)
            r.raise_for_status()
            return r.json() if r.content else None

    async def delete(self, path: str, user_token: str | None = None, **params: Any) -> None:
        url = f"{self.base_url}{path}"
        headers = dict(self._headers)
        if user_token:
            headers["Authorization"] = _AUTH_HDR.format(token=user_token)
        params = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(url, params=params, headers=headers)
            r.raise_for_status()

    # ── user token (for playlist writes) ─────────────────────────────────────

    async def get_user_token(self, username: str, password: str) -> str | None:
        """Return a cached user-scoped token, refreshing if expired."""
        async with _token_lock:
            cached = _user_token_cache.get(username)
            if cached and time.time() < cached[1]:
                return cached[0]

            url = f"{self.base_url}/Users/AuthenticateByName"
            try:
                async with httpx.AsyncClient(timeout=15.0) as c:
                    r = await c.post(
                        url,
                        json={"Username": username, "Pw": password},
                        headers={
                            "Authorization": (
                                'MediaBrowser Client="Octave-Subsonic", '
                                'DeviceId="octave-subsonic-01", '
                                'Device="Octave", Version="1.0.0"'
                            )
                        },
                    )
                    r.raise_for_status()
                    token = r.json().get("AccessToken")
                    if token:
                        _user_token_cache[username] = (token, time.time() + _TOKEN_TTL)
                        return token
            except Exception as exc:
                log.warning("[subsonic] user token refresh failed: %s", exc)
            return None

    # ── stream proxy ──────────────────────────────────────────────────────────

    async def stream_audio(
        self,
        item_id: str,
        range_header: str | None = None,
        max_bit_rate: int | None = None,
        fmt: str | None = None,
    ) -> tuple[int, dict, AsyncIterator[bytes]]:
        """Stream audio bytes from Jellyfin.

        Returns (status_code, response_headers, byte_iterator).
        Explicitly fixes Jellyfin 10.11's video/quicktime Content-Type bug for M4A.
        """
        params: dict[str, Any] = {"static": "true"}
        if max_bit_rate and max_bit_rate > 0:
            params["MaxStreamingBitrate"] = max_bit_rate * 1000
        if fmt and fmt not in ("raw", ""):
            params["AudioCodec"] = fmt

        url = f"{self.base_url}/Audio/{item_id}/stream"
        req_headers = dict(self._headers)
        if range_header:
            req_headers["Range"] = range_header

        client = httpx.AsyncClient(timeout=None)  # no timeout for streaming
        req = client.build_request("GET", url, params=params, headers=req_headers)
        resp = await client.send(req, stream=True)

        # Build response headers — fix Content-Type if Jellyfin lies about M4A
        content_type = _fix_content_type(resp.headers.get("content-type", ""), item_id)
        out_headers = {
            "Content-Type": content_type,
            "Accept-Ranges": "bytes",
        }
        for h in ("Content-Length", "Content-Range", "ETag", "Last-Modified"):
            if h in resp.headers:
                out_headers[h] = resp.headers[h]

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return resp.status_code, out_headers, _iter()

    # ── music endpoints ───────────────────────────────────────────────────────

    async def get_music_folders(self) -> list[dict]:
        data = await self.get("/Library/MediaFolders")
        return [
            i for i in data.get("Items", [])
            if i.get("CollectionType") == "music"
        ]

    async def get_artists(self) -> dict:
        # Jellyfin's bulk AlbumArtists endpoint never populates ChildCount/AlbumCount,
        # so we run a parallel album query and build a count map ourselves.
        artists_task = self.get(
            "/Artists/AlbumArtists",
            UserId=self.user_id,
            Recursive=True,
            SortBy="SortName",
            SortOrder="Ascending",
            Fields="ItemCounts,PrimaryImageAspectRatio,BasicSyncInfo,UserData",
            Limit=10000,
        )
        albums_task = self.get(
            "/Items",
            UserId=self.user_id,
            IncludeItemTypes="MusicAlbum",
            Recursive=True,
            Fields="ArtistItems,ChildCount",
            Limit=50000,
        )
        artists_data, albums_data = await asyncio.gather(artists_task, albums_task)

        # Build artist_id → album_count map from album ArtistItems
        album_counts: dict[str, int] = {}
        for album in albums_data.get("Items", []):
            for a in album.get("ArtistItems", []):
                aid = a.get("Id")
                if aid:
                    album_counts[aid] = album_counts.get(aid, 0) + 1

        # Inject album_count back onto artist items
        for artist in artists_data.get("Items", []):
            artist["_AlbumCount"] = album_counts.get(artist["Id"], 0)

        return artists_data

    async def get_artist(self, artist_id: str) -> dict:
        _ALBUM_FIELDS = "BasicSyncInfo,UserData,ItemCounts,ChildCount"
        item_task = self.get(f"/Items/{artist_id}", UserId=self.user_id, Fields="BasicSyncInfo,UserData")
        albums_task = self.get(
            "/Items",
            UserId=self.user_id,
            AlbumArtistIds=artist_id,
            IncludeItemTypes="MusicAlbum",
            Recursive=True,
            SortBy="ProductionYear,SortName",
            Fields=_ALBUM_FIELDS,
        )
        item, albums = await asyncio.gather(item_task, albums_task)
        album_list = albums.get("Items", [])

        if not album_list:
            # Fallback: derive albums from the artist's tracks (handles libraries
            # where the album item has no AlbumArtistIds set — e.g. flat folder
            # imports where only track-level artist tags are present).
            tracks = await self.get(
                "/Items",
                UserId=self.user_id,
                ArtistIds=artist_id,
                IncludeItemTypes="Audio",
                Recursive=True,
                Fields="AlbumId",
                Limit=10000,
            )
            album_ids = list({
                t["AlbumId"] for t in tracks.get("Items", []) if t.get("AlbumId")
            })
            if album_ids:
                batch = await self.get(
                    "/Items",
                    UserId=self.user_id,
                    Ids=",".join(album_ids),
                    Fields=_ALBUM_FIELDS,
                    SortBy="ProductionYear,SortName",
                )
                album_list = batch.get("Items", [])

        return {"item": item, "albums": album_list}

    async def get_album(self, album_id: str) -> dict:
        item = await self.get(f"/Items/{album_id}", UserId=self.user_id, Fields="BasicSyncInfo,UserData,MediaStreams,ChildCount")
        tracks = await self.get(
            "/Items",
            UserId=self.user_id,
            ParentId=album_id,
            IncludeItemTypes="Audio",
            Recursive=True,
            SortBy="ParentIndexNumber,IndexNumber",
            Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams",
        )
        return {"item": item, "tracks": tracks.get("Items", [])}

    async def get_song(self, song_id: str) -> dict:
        return await self.get(
            f"/Items/{song_id}",
            UserId=self.user_id,
            Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams",
        )

    async def get_albums(
        self,
        list_type: str = "alphabeticalByName",
        size: int = 10,
        offset: int = 0,
        from_year: int | None = None,
        to_year: int | None = None,
        genre: str | None = None,
    ) -> list[dict]:
        sort_map = {
            "alphabeticalByName": ("SortName", "Ascending"),
            "alphabeticalByArtist": ("AlbumArtist,SortName", "Ascending"),
            "byYear": ("ProductionYear", "Ascending" if from_year and to_year and from_year <= to_year else "Descending"),
            "newest": ("DateCreated", "Descending"),
            "recent": ("DatePlayed", "Descending"),
            "frequent": ("PlayCount", "Descending"),
            "highest": ("CommunityRating", "Descending"),
            "random": ("Random", "Ascending"),
            "starred": ("SortName", "Ascending"),
        }
        sort_by, sort_order = sort_map.get(list_type, ("SortName", "Ascending"))

        extra: dict[str, Any] = {}
        if list_type == "byYear":
            if from_year:
                extra["MinYear"] = from_year
            if to_year:
                extra["MaxYear"] = to_year
        if list_type == "starred":
            extra["Filters"] = "IsFavorite"
        if genre:
            extra["Genres"] = genre

        data = await self.get(
            "/Items",
            UserId=self.user_id,
            IncludeItemTypes="MusicAlbum",
            Recursive=True,
            SortBy=sort_by,
            SortOrder=sort_order,
            Limit=size,
            StartIndex=offset,
            Fields="BasicSyncInfo,UserData,ItemCounts,ChildCount",
            **extra,
        )
        return data.get("Items", [])

    async def get_random_songs(self, size: int = 10, genre: str | None = None,
                                from_year: int | None = None, to_year: int | None = None) -> list[dict]:
        extra: dict[str, Any] = {}
        if genre:
            extra["Genres"] = genre
        if from_year:
            extra["MinYear"] = from_year
        if to_year:
            extra["MaxYear"] = to_year
        data = await self.get(
            "/Items",
            UserId=self.user_id,
            IncludeItemTypes="Audio",
            Recursive=True,
            SortBy="Random",
            Limit=size,
            Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams",
            **extra,
        )
        return data.get("Items", [])

    async def search(self, query: str, artist_count: int = 20, artist_offset: int = 0,
                     album_count: int = 20, album_offset: int = 0,
                     song_count: int = 20, song_offset: int = 0) -> dict:
        artists_task = self.get("/Artists", UserId=self.user_id, SearchTerm=query,
                                Limit=artist_count, StartIndex=artist_offset,
                                Fields="BasicSyncInfo,UserData")
        albums_task = self.get("/Items", UserId=self.user_id, SearchTerm=query,
                               IncludeItemTypes="MusicAlbum", Recursive=True,
                               Limit=album_count, StartIndex=album_offset,
                               Fields="BasicSyncInfo,UserData,ItemCounts")
        songs_task = self.get("/Items", UserId=self.user_id, SearchTerm=query,
                              IncludeItemTypes="Audio", Recursive=True,
                              Limit=song_count, StartIndex=song_offset,
                              Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams")
        artists, albums, songs = await asyncio.gather(artists_task, albums_task, songs_task)
        return {
            "artists": artists.get("Items", []),
            "albums": albums.get("Items", []),
            "songs": songs.get("Items", []),
        }

    async def get_genres(self) -> list[dict]:
        data = await self.get(
            "/MusicGenres",
            UserId=self.user_id,
            Recursive=True,
            SortBy="SortName",
            Fields="ItemCounts",
            Limit=500,
        )
        return data.get("Items", [])

    async def get_starred(self) -> dict:
        artists_task = self.get("/Artists", UserId=self.user_id, Filters="IsFavorite",
                                Fields="BasicSyncInfo,UserData", Limit=500)
        albums_task = self.get("/Items", UserId=self.user_id, Filters="IsFavorite",
                               IncludeItemTypes="MusicAlbum", Recursive=True,
                               Fields="BasicSyncInfo,UserData,ItemCounts", Limit=500)
        songs_task = self.get("/Items", UserId=self.user_id, Filters="IsFavorite",
                              IncludeItemTypes="Audio", Recursive=True,
                              Fields="BasicSyncInfo,UserData,MediaSources", Limit=500)
        artists, albums, songs = await asyncio.gather(artists_task, albums_task, songs_task)
        return {
            "artists": artists.get("Items", []),
            "albums": albums.get("Items", []),
            "songs": songs.get("Items", []),
        }

    async def get_playlists(self) -> list[dict]:
        data = await self.get("/Playlists", UserId=self.user_id, Fields="ChildCount,UserData")
        return data.get("Items", [])

    async def get_playlist(self, playlist_id: str) -> dict:
        item = await self.get(f"/Items/{playlist_id}", UserId=self.user_id)
        tracks = await self.get(
            f"/Playlists/{playlist_id}/Items",
            UserId=self.user_id,
            Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams",
            Limit=10000,
        )
        return {"item": item, "tracks": tracks.get("Items", [])}

    async def create_playlist(self, name: str, song_ids: list[str],
                               user_token: str | None = None) -> dict:
        data = await self.post(
            "/Playlists",
            json={"Name": name, "Ids": song_ids, "UserId": self.user_id, "MediaType": "Audio"},
            user_token=user_token,
        )
        return data or {}

    async def update_playlist(self, playlist_id: str, name: str | None = None,
                               add_ids: list[str] | None = None,
                               remove_indices: list[int] | None = None,
                               user_token: str | None = None) -> None:
        if name is not None:
            await self.post(f"/Items/{playlist_id}", json={"Name": name}, user_token=user_token)
        if add_ids:
            await self.post(f"/Playlists/{playlist_id}/Items",
                            json={"Ids": add_ids}, user_token=user_token)
        if remove_indices:
            for idx in sorted(remove_indices, reverse=True):
                await self.delete(f"/Playlists/{playlist_id}/Items",
                                  user_token=user_token, EntryIds=str(idx))

    async def delete_playlist(self, playlist_id: str, user_token: str | None = None) -> None:
        await self.delete(f"/Items/{playlist_id}", user_token=user_token)

    async def star(self, item_id: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(
                f"{self.base_url}/UserFavoriteItems/{item_id}",
                params={"UserId": self.user_id},
                headers=self._headers,
            )

    async def unstar(self, item_id: str) -> None:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.delete(
                f"{self.base_url}/UserFavoriteItems/{item_id}",
                params={"UserId": self.user_id},
                headers=self._headers,
            )

    async def set_rating(self, item_id: str, rating: int) -> None:
        """Set rating 1-5 → Jellyfin stores 0-10."""
        jf_rating = min(10, max(0, rating * 2))
        await self.post(f"/Users/{self.user_id}/Items/{item_id}/Rating",
                        json={"Likes": None, "FavoriteRating": jf_rating})

    async def scrobble(self, item_id: str, submission: bool = True,
                        position_ms: int | None = None) -> None:
        if submission:
            # Mark as played
            async with httpx.AsyncClient(timeout=15.0) as c:
                await c.post(
                    f"{self.base_url}/Users/{self.user_id}/PlayedItems/{item_id}",
                    headers=self._headers,
                )
        else:
            # Now playing — report playback start
            body: dict[str, Any] = {"ItemId": item_id, "CanSeek": True, "IsPaused": False}
            if position_ms is not None:
                body["PositionTicks"] = position_ms * 10000
            async with httpx.AsyncClient(timeout=15.0) as c:
                await c.post(
                    f"{self.base_url}/Sessions/Playing",
                    json=body,
                    headers=self._headers,
                )

    async def get_similar_songs(self, item_id: str, count: int = 50) -> list[dict]:
        data = await self.get(
            f"/Items/{item_id}/Similar",
            UserId=self.user_id,
            Limit=count,
            Fields="BasicSyncInfo,UserData,MediaSources",
        )
        return data.get("Items", [])

    async def get_top_songs(self, artist_id: str, count: int = 50) -> list[dict]:
        data = await self.get(
            "/Items",
            UserId=self.user_id,
            ArtistIds=artist_id,
            IncludeItemTypes="Audio",
            Recursive=True,
            SortBy="PlayCount",
            SortOrder="Descending",
            Limit=count,
            Fields="BasicSyncInfo,UserData,MediaSources,MediaStreams",
        )
        return data.get("Items", [])


# ── content-type fix ──────────────────────────────────────────────────────────

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


def _fix_content_type(jellyfin_ct: str, _item_id: str) -> str:
    """Jellyfin 10.11 returns video/quicktime for M4A — correct it."""
    if "video/quicktime" in jellyfin_ct or "video/" in jellyfin_ct:
        return "audio/mp4"
    for ext, mime in _MIME.items():
        if ext in jellyfin_ct:
            return mime
    return jellyfin_ct or "audio/mpeg"
