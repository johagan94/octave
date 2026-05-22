import json

from octave.web.routes.sync import download_missing_csv


def test_missing_csv_escapes_formula_cells(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "missing_tracks.json").write_text(json.dumps({
        "playlist-1": {
            "tracks": [{
                "spotify_id": "track-1",
                "title": "=IMPORTXML(\"http://example.test\")",
                "artist": "+SUM(1,1)",
                "album": "@cmd",
                "album_type": "-1",
                "spotify_url": "https://open.spotify.com/track/track-1",
            }],
        },
    }))

    response = download_missing_csv("playlist-1")
    body = response.body.decode()

    assert "'=IMPORTXML" in body
    assert "'+SUM(1,1)" in body
    assert "'@cmd" in body
    assert "'-1" in body
