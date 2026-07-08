import logging
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routes  # noqa: E402


@pytest.fixture
def dlc_dir(tmp_path):
    d = tmp_path / "dlc"
    d.mkdir()
    return d


@pytest.fixture
def client(dlc_dir):
    app = FastAPI()
    context = {"get_dlc_dir": lambda: dlc_dir, "log": logging.getLogger("test")}
    routes.setup(app, context)
    with TestClient(app) as c:
        yield c


def write_arrangement_xml(song_dir, name, offset="0", original_comment=None, edited_comments=None):
    song_dir.mkdir(parents=True, exist_ok=True)
    comments = ""
    if original_comment is not None:
        comments += f"<!-- original_offset: {original_comment} -->\n  "
    for e in (edited_comments or []):
        comments += f"<!-- edited_offset: {e} -->\n  "
    xml = f"""<?xml version="1.0"?>
<song>
  {comments}<offset>{offset}</offset>
</song>
"""
    path = song_dir / name
    path.write_text(xml, encoding="utf-8")
    return path
