"""Sync status/save/revert routes — exercised through the HTTP surface
since the offset-editing helpers are nested inside setup()."""

from conftest import write_arrangement_xml

STATUS = "/api/plugins/loose_folder/sync_status"
SAVE = "/api/plugins/loose_folder/sync_save"
REVERT = "/api/plugins/loose_folder/sync_revert"


def test_sync_status_missing_song_dir_404(client):
    assert client.get(STATUS, params={"filename": "nope"}).status_code == 404


def test_sync_status_no_xml_files_404(client, dlc_dir):
    (dlc_dir / "song").mkdir()
    assert client.get(STATUS, params={"filename": "song"}).status_code == 404


def test_sync_status_only_excluded_arrangements_404(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "vocals.xml", offset="1.0")
    write_arrangement_xml(song_dir, "showlights.xml", offset="1.0")
    assert client.get(STATUS, params={"filename": "song"}).status_code == 404


def test_sync_status_uncorrected(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "lead.xml", offset="0.5")
    r = client.get(STATUS, params={"filename": "song"})
    assert r.status_code == 200
    body = r.json()
    assert body["current_offset"] == 0.5
    assert body["original_offset"] == 0.5
    assert body["has_correction"] is False


def test_sync_status_with_correction(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "lead.xml", offset="1.2", original_comment="0.5")
    r = client.get(STATUS, params={"filename": "song"})
    body = r.json()
    assert body["current_offset"] == 1.2
    assert body["original_offset"] == 0.5
    assert body["has_correction"] is True


def test_sync_status_rejects_path_traversal(client, dlc_dir):
    r = client.get(STATUS, params={"filename": "../outside"})
    assert r.status_code == 404


def test_sync_save_requires_filename_and_offset(client):
    assert client.post(SAVE, json={"offset": 1.0}).status_code == 400
    assert client.post(SAVE, json={"filename": "song"}).status_code == 400


def test_sync_save_missing_song_404(client):
    assert client.post(SAVE, json={"filename": "nope", "offset": 1.0}).status_code == 404


def test_sync_save_invalid_offset_400(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    r = client.post(SAVE, json={"filename": "song", "offset": "not-a-number"})
    assert r.status_code == 400


def test_sync_save_applies_and_seeds_original_offset(client, dlc_dir):
    song_dir = dlc_dir / "song"
    lead = write_arrangement_xml(song_dir, "lead.xml", offset="0.0")

    r = client.post(SAVE, json={"filename": "song", "offset": 1.5})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == ["lead.xml"]
    assert body["skipped"] == []

    text = lead.read_text(encoding="utf-8")
    assert "<offset>1.5</offset>" in text
    assert "original_offset: 0" in text
    assert "edited_offset: 1.5" in text


def test_sync_save_excludes_vocal_and_showlight_xmls(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    vocals = write_arrangement_xml(song_dir, "vocals.xml", offset="0.0")

    r = client.post(SAVE, json={"filename": "song", "offset": 2.0})
    assert r.json()["updated"] == ["lead.xml"]
    assert "<offset>0.0</offset>" in vocals.read_text(encoding="utf-8")


def test_sync_save_only_updates_arrangements_sharing_the_original_offset(client, dlc_dir):
    song_dir = dlc_dir / "song"
    # lead already corrected once (original=0.0, current=1.0); rhythm untouched at 0.0.
    write_arrangement_xml(song_dir, "lead.xml", offset="1.0", original_comment="0.0")
    write_arrangement_xml(song_dir, "rhythm.xml", offset="0.0")

    r = client.post(SAVE, json={"filename": "song", "offset": 2.0})
    body = r.json()
    # lead's effective_original (0.0) matches rhythm's effective_original (0.0)
    # -> match_value derived from the FIRST xml alphabetically (lead.xml).
    assert set(body["updated"]) == {"lead.xml", "rhythm.xml"}


def test_sync_save_second_edit_appends_another_edited_comment(client, dlc_dir):
    song_dir = dlc_dir / "song"
    lead = write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    client.post(SAVE, json={"filename": "song", "offset": 1.0})
    client.post(SAVE, json={"filename": "song", "offset": 2.0})

    text = lead.read_text(encoding="utf-8")
    assert text.count("edited_offset:") == 2
    assert text.count("original_offset:") == 1
    assert "<offset>2</offset>" in text


def test_sync_revert_missing_filename_400(client):
    assert client.post(REVERT, json={}).status_code == 400


def test_sync_revert_missing_song_404(client):
    assert client.post(REVERT, json={"filename": "nope"}).status_code == 404


def test_sync_revert_all_restores_original(client, dlc_dir):
    song_dir = dlc_dir / "song"
    lead = write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    client.post(SAVE, json={"filename": "song", "offset": 1.0})
    client.post(SAVE, json={"filename": "song", "offset": 2.0})

    r = client.post(REVERT, json={"filename": "song", "mode": "all"})
    assert r.status_code == 200
    assert r.json()["reverted"] == ["lead.xml"]
    assert "<offset>0.0</offset>" in lead.read_text(encoding="utf-8")


def test_sync_revert_step_undoes_one_edit_at_a_time(client, dlc_dir):
    song_dir = dlc_dir / "song"
    lead = write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    client.post(SAVE, json={"filename": "song", "offset": 1.0})
    client.post(SAVE, json={"filename": "song", "offset": 2.0})

    client.post(REVERT, json={"filename": "song", "mode": "step"})
    assert "<offset>1</offset>" in lead.read_text(encoding="utf-8")

    client.post(REVERT, json={"filename": "song", "mode": "step"})
    assert "<offset>0.0</offset>" in lead.read_text(encoding="utf-8")


def test_sync_revert_step_with_no_edits_is_noop(client, dlc_dir):
    song_dir = dlc_dir / "song"
    write_arrangement_xml(song_dir, "lead.xml", offset="0.0")
    r = client.post(REVERT, json={"filename": "song", "mode": "step"})
    assert r.json()["reverted"] == []
