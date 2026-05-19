"""GET/PUT /api/config — read and write the raw config.json.

We don't validate the body strictly — the user's config has fields the
API doesn't model (custom playlist annotations, future knobs). On PUT
we round-trip through json so syntactically broken bodies fail early,
but we don't enforce a schema.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body

from ...config import config_path
from ..envelope import err, ok
from ..models import ConfigPayload

router = APIRouter()


@router.get("/config")
def get_config():
    path = config_path()
    if not path.exists():
        return ok(ConfigPayload(config={}))
    try:
        with path.open() as fh:
            cfg = json.load(fh)
        return ok(ConfigPayload(config=cfg))
    except json.JSONDecodeError as exc:
        return err("config_invalid_json", str(exc), status=500)
    except OSError as exc:
        return err("config_unreadable", str(exc), status=500)


@router.put("/config")
def put_config(body: dict = Body(...)):
    path = config_path()
    if not isinstance(body, dict):
        return err("config_must_be_object", "config must be a JSON object", status=400)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w") as fh:
            json.dump(body, fh, indent=2)
        tmp.replace(path)  # atomic on POSIX
    except OSError as exc:
        return err("config_write_failed", str(exc), status=500)

    return ok(ConfigPayload(config=body))
