"""loosefolder.py — treat a directory of raw RS2014 assets as a playable song.

Expected layout (artist/album/song_dir structure is optional but used for
metadata inference when manifest.json is absent):

    song_dir/
        audio.wem          (required)
        lead.xml           (at least one arrangement XML required)
        rhythm.xml
        bass.xml
        manifest.json      (optional)
        album_art.jpg / cover.jpg / album.png  (optional)

manifest.json fields (all optional — XML metadata fills in gaps):
    {
        "title":          "Song Name",
        "artist":         "Artist Name",
        "album":          "Album Name",
        "year":           "2024",
        "tuning_offsets": [0, 0, 0, 0, 0, 0]
    }
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

AUDIO_NAMES = ["audio.wem", "song.wem"]
ART_NAMES   = ["album_art.jpg", "album_art.png",
               "cover.jpg", "cover.png", "album.jpg", "album.png",
               "art.jpg", "art.png", "folder.jpg", "folder.png"]

# Arrangement type detection from filename keywords or <arrangement> tag
# Format: keyword -> (type, display_name, sort_priority)
ARR_TYPE_MAP = {
    "lead":     ("lead",   "Lead",   0),
    "rhythm":   ("rhythm", "Rhythm", 2),
    "bass":     ("bass",   "Bass",   3),
    "combo":    ("combo",  "Combo",  1),
    "chord":    ("combo",  "Combo",  1),
    "humstrum": ("combo",  "Combo",  1),
}


def is_loose_song(path: Path) -> bool:
    """True if this directory looks like a loose song folder."""
    if not path.is_dir():
        return False
    has_audio = (
        any((path / a).exists() for a in AUDIO_NAMES) or
        any(True for f in path.glob("*.wem") if "preview" not in f.stem.lower())
    )
    has_xml = bool(list(path.glob("*.xml")))
    return has_audio and has_xml


def find_audio(path: Path) -> Path | None:
    """Return the path to the best audio file in the folder.
    Prefers known names, then falls back to any WEM that isn't a preview clip."""
    # Check known names first
    named = next((path / a for a in AUDIO_NAMES if (path / a).exists()), None)
    if named:
        return named
    # Fall back to any WEM that doesn't have 'preview' in the name,
    # sorted largest first (full song > short clip)
    candidates = sorted(
        [f for f in path.glob("*.wem") if "preview" not in f.stem.lower()],
        key=lambda f: f.stat().st_size,
        reverse=True,
    )
    return candidates[0] if candidates else None


def find_art(path: Path) -> Path | None:
    """Return the path to the first recognised album art file in the folder."""
    return next((path / a for a in ART_NAMES if (path / a).exists()), None)


def _arr_type_from_filename(stem: str) -> tuple:
    """Infer arrangement type from filename keywords."""
    s = stem.lower()
    for key, val in ARR_TYPE_MAP.items():
        if key in s:
            return val
    return ("lead", "Lead", 0)  # fallback


def _parse_xml_meta(xml_path: Path) -> dict:
    """Parse a Rocksmith arrangement XML and return song-level metadata."""
    try:
        root = ET.parse(str(xml_path)).getroot()
        if root.tag != "song":
            return {}

        def txt(tag, default=""):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else default

        # Tuning from attributes
        tuning_el = root.find("tuning")
        if tuning_el is not None:
            offsets = [int(tuning_el.get(f"string{i}", 0)) for i in range(6)]
        else:
            offsets = [0] * 6

        # Arrangement type — filename is more reliable than the XML tag
        # because some authoring tools write "Lead" for all arrangements.
        # We read the XML tag here and let _detect_arrangements decide
        # which source to trust.
        arr_tag = txt("arrangement", "").lower()
        arr_from_tag = ARR_TYPE_MAP.get(arr_tag, None)

        duration = 0.0
        try:
            duration = float(txt("songLength", "0"))
        except (ValueError, TypeError):
            pass

        return {
            "title":          txt("title"),
            "artist":         txt("artistName"),
            "album":          txt("albumName"),
            "year":           txt("albumYear", ""),
            "duration":       duration,
            "tuning_offsets": offsets,
            "arr_from_tag":   arr_from_tag,  # may be None
        }
    except Exception:
        return {}


def _detect_arrangements(path: Path) -> tuple[list[dict], dict]:
    """
    Parse all arrangement XMLs.
    Returns (arrangements_list, shared_meta).
    shared_meta contains title/artist/album/year/duration/tuning_offsets
    taken from the first XML that has them.
    """
    arrangements = []
    shared_meta = {}

    for xml in sorted(path.glob("*.xml")):
        stem = xml.stem.lower()
        if "vocal" in stem or "showlight" in stem:
            continue

        meta = _parse_xml_meta(xml)
        if not meta:
            continue

        # Take song-level fields from the first XML that has them
        if not shared_meta.get("title") and meta.get("title"):
            shared_meta = {k: meta[k] for k in
                           ("title", "artist", "album", "year",
                            "duration", "tuning_offsets")}

        # Arrangement type: prefer filename keywords over the XML tag
        # because some tools write "Lead" for all arrangements regardless
        # of actual type. Only fall back to the XML tag when the filename
        # gives no useful signal (i.e. no recognisable keyword found).
        filename_type = _arr_type_from_filename(stem)
        if filename_type[0] != "lead" or "lead" in stem:
            # Filename gave a confident answer
            arr_type, arr_name, priority = filename_type
        else:
            # Filename wasn't specific — try the XML tag
            arr_from_tag = meta.get("arr_from_tag")
            if arr_from_tag:
                arr_type, arr_name, priority = arr_from_tag
            else:
                arr_type, arr_name, priority = filename_type

        arrangements.append({
            "type":     arr_type,
            "name":     arr_name,
            "file":     xml.name,
            "priority": priority,
        })

    arrangements.sort(key=lambda a: a["priority"])
    for i, a in enumerate(arrangements):
        a["index"] = i
        del a["priority"]

    return arrangements, shared_meta


def _has_lyrics(path: Path) -> bool:
    """Return True if any XML in the folder is a vocals track."""
    for xml in path.glob("*.xml"):
        try:
            if ET.parse(str(xml)).getroot().tag == "vocals":
                return True
        except Exception:
            pass
    return False


def extract_meta(path: Path) -> dict:
    """Return metadata dict for a loose song folder.

    Priority chain:
      1. manifest.json (explicit user-supplied data)
      2. XML metadata  (parsed from arrangement XMLs)
      3. Folder name   (last resort inference from directory structure)
    """
    # 1. Try manifest.json
    manifest_path = path / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 2. Parse XMLs for song metadata + arrangements
    arrangements, xml_meta = _detect_arrangements(path)

    # 3. Merge — manifest wins over XML, XML wins over folder inference
    parts = path.parts
    title  = manifest.get("title")  or xml_meta.get("title")  or path.name
    artist = manifest.get("artist") or xml_meta.get("artist") or (parts[-3] if len(parts) >= 3 else "")
    album  = manifest.get("album")  or xml_meta.get("album")  or (parts[-2] if len(parts) >= 2 else "")
    year   = str(manifest.get("year", "") or xml_meta.get("year", ""))
    duration       = float(manifest.get("duration", 0) or xml_meta.get("duration", 0))
    tuning_offsets = manifest.get("tuning_offsets") or xml_meta.get("tuning_offsets") or [0] * 6

    if manifest.get("arrangements"):
        arrangements = manifest["arrangements"]

    audio = find_audio(path)
    art   = find_art(path)

    return {
        "title":          title,
        "artist":         artist,
        "album":          album,
        "year":           year,
        "duration":       duration,
        "tuning_offsets": tuning_offsets,
        "arrangements":   arrangements,
        "audio_path":     str(audio) if audio else None,
        "art_path":       str(art)   if art   else None,
        "has_lyrics":     _has_lyrics(path),
    }
