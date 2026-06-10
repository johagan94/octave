import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octave.web import settings as settings_mod


def test_secrets_are_masked_not_returned_raw(tmp_path):
    """GET /api/settings must never echo a raw secret back to the client."""
    (tmp_path / "settings.json").write_text(json.dumps({
        "LIDARR_API_KEY": "supersecretkey123",
        "LASTFM_USERNAME": "bob",
    }))
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False):
        os.environ.pop("LIDARR_API_KEY", None)  # ensure file source
        os.environ.pop("LASTFM_USERNAME", None)
        allv = settings_mod.get_all_settings()

    secret = allv["LIDARR_API_KEY"]
    assert secret["value"] == ""                       # never raw
    assert secret["is_set"] is True
    assert secret["masked"]                             # has a masked preview
    assert "supersecretkey123" not in secret["masked"]
    # Non-secret knobs still return their value for the UI.
    assert allv["LASTFM_USERNAME"]["value"] == "bob"


def test_save_settings_hot_reload_applies_repeated_edits(tmp_path):
    """Editing a file-managed setting twice must take effect both times — the
    previous code mirrored the first value into os.environ and then ignored
    later edits until a container restart."""
    key = "LASTFM_USERNAME"
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path)}, clear=False):
        os.environ.pop(key, None)
        settings_mod._injected_env_keys.discard(key)

        settings_mod.save_settings({key: "first"})
        assert os.environ[key] == "first"

        settings_mod.save_settings({key: "second"})
        assert os.environ[key] == "second"

        settings_mod.save_settings({key: ""})           # clear
        assert key not in os.environ


def test_save_settings_never_overrides_container_env(tmp_path):
    """A value the container set in the real environment keeps priority and is
    not clobbered by a UI save (documented env > file precedence)."""
    key = "TZ"
    with patch.dict(os.environ, {"SYNC_DATA_DIR": str(tmp_path), key: "UTC"}, clear=False):
        settings_mod._injected_env_keys.discard(key)
        settings_mod.save_settings({key: "Australia/Brisbane"})
        assert os.environ[key] == "UTC"  # container env untouched
