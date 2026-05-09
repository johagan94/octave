import os
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

auth_manager = SpotifyOAuth(
    client_id=os.environ["SPOTIFY_CLIENT_ID"],
    client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
    redirect_uri=os.environ["SPOTIFY_REDIRECT_URI"],
    scope="playlist-read-private playlist-read-collaborative",
    cache_path=".spotify_token_cache",
    open_browser=False,
)

# If no cached token, do manual auth
if not auth_manager.get_cached_token():
    print("\nOpen this URL in your browser:\n")
    print(auth_manager.get_authorize_url())
    print("\nAfter approving, paste the full redirect URL here:")
    response_url = input("> ").strip()
    code = auth_manager.parse_response_code(response_url)
    auth_manager.get_access_token(code)

sp = spotipy.Spotify(auth_manager=auth_manager)

results = sp.current_user_playlists()
playlists = []
while results:
    for p in results["items"]:
        playlists.append({"id": p["id"], "name": p["name"], "tracks": p["tracks"]["total"]})
    results = sp.next(results) if results["next"] else None

print(f"\nFound {len(playlists)} playlists:\n")
for p in playlists:
    print(f"  {p['tracks']:>4} tracks  |  {p['id']}  |  {p['name']}")
