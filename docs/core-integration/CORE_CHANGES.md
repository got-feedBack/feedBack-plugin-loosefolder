# Loose Folder Support — Contribution Notes

## Overview

This document describes proposed changes to add native support for **loose song
folders** as a first-class format alongside PSARC and Sloppak.

A loose folder is a directory containing raw Rocksmith 2014 CDLC assets — arrangement
XMLs, a WEM audio file, and optional album art — that have not yet been packaged into
a PSARC or Sloppak container. This is a common state for charts during authoring and
testing, where a creator has all the source assets ready but hasn't gone through the
packaging step. Supporting this format lets those charts be played and tested directly
in Slopsmith without any conversion.

---

## Architecture: why core changes are needed alongside the plugin

The companion plugin (`plugins/loose_folder/`) provides the Fix Sync UI described at
the end of this document. It is self-contained and optional.

However, the plugin alone is not sufficient to enable loose folder support. The plugin
system's `routes.py` can register new API endpoints, but it cannot hook into the
background scanner or the highway WebSocket handler — both of which live in
`server.py`. Without the core changes below:

- The scanner will not discover loose folders during library import
- The highway WebSocket will not know how to load, stream audio from, or serve art
  for a loose folder song
- The `format` field sent to the frontend will be incorrect, preventing the plugin's
  UI from activating on the right songs

The changes to `server.py` and `lib/loosefolder.py` are therefore a required
foundation. The plugin builds on top of them.

---

## New file: `lib/loosefolder.py`

Drop this into the `lib/` directory alongside `sloppak.py`, `song.py`, etc.

It provides:

- `is_loose_song(path)` — returns `True` if a directory contains at least one
  non-preview WEM file and at least one arrangement XML
- `extract_meta(path)` — parses song metadata with a priority chain of:
  `manifest.json` → XML tags (`<title>`, `<artistName>`, `<albumName>`,
  `<albumYear>`, `<songLength>`, `<tuning>`) → folder name inference
- `find_audio(path)` — returns the path to the best WEM file in the folder.
  Checks `audio.wem` and `song.wem` by name first, then falls back to any WEM
  that does not have "preview" in its filename, picking the largest file
- `find_art(path)` — returns the path to album art, checking `album_art.jpg`,
  `cover.jpg`, `album.jpg`, and common variants
- `_detect_arrangements(path)` — infers arrangement type primarily from filename
  keywords (`lead`, `rhythm`, `bass`, `chord`), falling back to the `<arrangement>`
  XML tag. Filename is preferred because some authoring tools write `Lead` in the
  tag for all arrangement types regardless of actual type

---

## Changes to `server.py`

### 1. Add import

**Location:** near the top of the file, after `import sloppak as sloppak_mod`

**Reason:** makes the loosefolder module available to all functions below.

```python
# Before:
import sloppak as sloppak_mod

# After:
import sloppak as sloppak_mod
import loosefolder as loosefolder_mod
```

---

### 2. Add `_extract_meta_loosefolder()` function

**Location:** directly after the existing `_extract_meta_sloppak()` function and
before `_extract_meta_for_file()`

**Reason:** follows the same pattern as `_extract_meta_sloppak()` — extracts
metadata, converts tuning offsets to a tuning name string, and tags the record
with its format.

```python
def _extract_meta_loosefolder(path: Path) -> dict:
    """Extract metadata for a loose song folder (raw XMLs + WEM audio)."""
    meta = loosefolder_mod.extract_meta(path)
    offsets = meta.pop("tuning_offsets", None) or [0] * 6
    meta["tuning"] = tuning_name(offsets)
    meta["format"] = "loose"
    return meta
```

---

### 3. Add dispatch branch in `_extract_meta_for_file()`

**Location:** at the very top of the `_extract_meta_for_file()` function body,
before the existing `if sloppak_mod.is_sloppak(psarc_path):` branch

**Reason:** the dispatcher must check for loose folders before attempting PSARC
extraction, since passing a directory path to the PSARC extractor would fail.

```python
# Before:
def _extract_meta_for_file(psarc_path: Path) -> dict:
    """Extract metadata — dispatches on extension; PSARC path tries fast then falls back."""
    if sloppak_mod.is_sloppak(psarc_path):

# After:
def _extract_meta_for_file(psarc_path: Path) -> dict:
    """Extract metadata — dispatches on extension; PSARC path tries fast then falls back."""
    if loosefolder_mod.is_loose_song(psarc_path):
        return _extract_meta_loosefolder(psarc_path)
    if sloppak_mod.is_sloppak(psarc_path):
```

---

### 4. Discover loose folders in `_background_scan()`

**Location:** directly after the existing sloppaks list comprehension and before
the `except PermissionError` block

**Reason:** the scanner currently only globs for `*.psarc` and `*.sloppak`. Loose
folders are discovered by finding all non-preview WEM files recursively and
collecting their parent directories that pass `is_loose_song()`.

```python
# Before:
        sloppaks = [f for f in sorted(dlc.rglob("*.sloppak"))
                    if sloppak_mod.is_sloppak(f)]
    except PermissionError as e:

# After:
        sloppaks = [f for f in sorted(dlc.rglob("*.sloppak"))
                    if sloppak_mod.is_sloppak(f)]

        # Loose song folders: any directory containing a non-preview *.wem + *.xml
        loose_songs = []
        seen_loose = set()
        for wem in sorted(dlc.rglob("*.wem")):
            if "preview" in wem.stem.lower():
                continue
            d = wem.parent
            if d not in seen_loose and loosefolder_mod.is_loose_song(d):
                loose_songs.append(d)
                seen_loose.add(d)

    except PermissionError as e:
```

---

### 5. Include loose folders in the scan queue

**Location:** the `all_songs` assignment and the two print statements in
`_background_scan()`

**Reason:** loose folder paths need to join the same scan queue as PSARCs and
sloppaks so they are scanned, cached, and deduplicated against the database.

```python
# Before:
    all_songs = psarcs + sloppaks
    print(f"Scan: listed {len(psarcs)} PSARCs and {len(sloppaks)} sloppaks in {dlc}", flush=True)

# After:
    all_songs = psarcs + sloppaks + loose_songs
    print(f"Scan: listed {len(psarcs)} PSARCs, {len(sloppaks)} sloppaks, "
          f"{len(loose_songs)} loose folders in {dlc}", flush=True)
```

Also update the second print statement:

```python
# Before:
    print(f"Library: {len(psarcs)} PSARCs + {len(sloppaks)} sloppaks, {len(all_songs) - len(to_scan)} cached, {len(to_scan)} to scan", flush=True)

# After:
    print(f"Library: {len(psarcs)} PSARCs + {len(sloppaks)} sloppaks + {len(loose_songs)} loose folders, "
          f"{len(all_songs) - len(to_scan)} cached, {len(to_scan)} to scan", flush=True)
```

---

### 6. Include `loose` in library format filters

**Location:** in `list_library()` and `list_artists()` endpoints

**Reason:** the format filter allowlist currently only accepts `psarc` and
`sloppak`. Adding `loose` lets the frontend filter the library by format.

```python
# list_library — Before:
    fmt = format if format in ("psarc", "sloppak") else ""

# list_library — After:
    fmt = format if format in ("psarc", "sloppak", "loose") else ""

# list_artists — same change applied to the equivalent line
```

---

### 7. Serve album art for loose folders in `get_song_art()`

**Location:** after the sloppak art block and before the `# Check cache first`
comment

**Reason:** loose folders store art as a plain image file on disk. No extraction
or caching is needed — the file can be served directly.

```python
    # Loose folder path: serve art file directly.
    if loosefolder_mod.is_loose_song(psarc_path):
        art_path = loosefolder_mod.find_art(psarc_path)
        if art_path and art_path.exists():
            mt = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
            }.get(art_path.suffix.lower(), "image/jpeg")
            return FileResponse(str(art_path), media_type=mt)
        return JSONResponse({"error": "no art"}, 404)

    # Check cache first
```

---

### 8. Add `is_loose` flag in `highway_ws()`

**Location:** on the line after `is_slop = sloppak_mod.is_sloppak(psarc_path)`

**Reason:** the highway handler needs to know the format early to branch correctly
for loading, audio conversion, and the format field in `song_info`.

```python
# Before:
    is_slop = sloppak_mod.is_sloppak(psarc_path)
    tmp = None

# After:
    is_slop = sloppak_mod.is_sloppak(psarc_path)
    is_loose = loosefolder_mod.is_loose_song(psarc_path)
    tmp = None
```

---

### 9. Load loose songs in `highway_ws()`

**Location:** in the extraction block that branches on `is_slop`. Add an
`elif is_loose:` branch between the sloppak and PSARC branches.

**Reason:** loose folders don't need extraction — `load_song()` can read
arrangement XMLs directly from a flat directory, the same function used after
PSARC unpacking.

```python
            elif is_loose:
                # Load directly from the folder — no extraction needed.
                song = await loop.run_in_executor(None, lambda: load_song(str(psarc_path)))
                tmp = str(psarc_path)
                owns_tmp = False
```

---

### 10. Handle audio conversion for loose folders in `highway_ws()`

**Location:** in the audio URL block that branches on `is_slop`. Add an
`elif is_loose:` branch between the sloppak and PSARC branches.

**Reason:** loose folders have a raw WEM that needs the same vgmstream conversion
pipeline as PSARCs, but the path comes from `loosefolder_mod.find_audio()` rather
than from inside an extracted temp directory. The converted file is cached in
`AUDIO_CACHE_DIR` using the same naming convention as PSARCs.

```python
        elif is_loose:
            AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for ext in [".mp3", ".ogg", ".wav"]:
                cached_audio = AUDIO_CACHE_DIR / f"audio_{audio_id}{ext}"
                if cached_audio.exists() and cached_audio.stat().st_size > 1000:
                    audio_url = f"/audio/audio_{audio_id}{ext}"
                    break
            if not audio_url:
                await websocket.send_json({"type": "loading", "stage": "Converting audio..."})
                wem_path = loosefolder_mod.find_audio(psarc_path)
                if wem_path:
                    try:
                        audio_path = convert_wem(str(wem_path), str(AUDIO_CACHE_DIR / f"audio_{audio_id}"))
                        audio_url = f"/audio/audio_{audio_id}{Path(audio_path).suffix}"
                    except Exception as e:
                        audio_error = f"Audio conversion failed: {e}"
                else:
                    audio_error = "No audio file found in loose folder."
```

Also update the guard that triggers PSARC audio extraction:

```python
# Before:
        if not audio_url and not is_slop:

# After:
        if not audio_url and not is_slop and not is_loose:
```

---

### 11. Send chart offset and update format field in `song_info`

**Location:** in the `await websocket.send_json({"type": "song_info", ...})` call

**Reason:** the `<offset>` value in arrangement XMLs defines how many seconds of
audio precede beat zero of the chart. CDLC charts in a loose/testing state may not
use the traditional silence-padded audio that PSARCs rely on, so the offset must be
read from the XML and communicated to the frontend to keep notes in sync with audio.
The offset is only applied for loose format songs — PSARC and Sloppak handle timing
differently and do not need this adjustment.

```python
# Add to the song_info dict:
            "offset": float(getattr(song, 'offset', 0.0)) if is_loose else 0.0,
            "format": "sloppak" if is_slop else ("loose" if is_loose else "psarc"),
```

---

### 12. Skip tone XML parsing for loose folders

**Location:** the tone changes XML path list in `highway_ws()`

**Reason:** tone data for loose folders lives in separate `tone_*.json` files
rather than inline in the arrangement XML. The existing tone XML parser should
be skipped to avoid false positives.

```python
# Before:
        if is_slop:
            xml_paths = []
        else:
            xml_paths = sorted(Path(tmp).rglob("*.xml"))

# After:
        if is_slop or is_loose:
            xml_paths = []
        else:
            xml_paths = sorted(Path(tmp).rglob("*.xml"))
```

---

## Changes to `static/highway.js`

### 13. Add `songOffset` variable

**Location:** after `let currentTime = 0;` near the top of the module

**Reason:** stores the per-song offset value received in `song_info`.

```javascript
    let currentTime = 0;
    let songOffset = 0.0;  // per-song chart offset from XML <offset> tag
```

---

### 14. Read offset from `song_info` message

**Location:** in the `case 'song_info':` handler, immediately after `songInfo = msg;`

**Reason:** captures the offset value sent by the server for each song.

```javascript
                    case 'song_info':
                        songInfo = msg;
                        songOffset = typeof msg.offset === 'number' ? msg.offset : 0.0;
```

---

### 15. Apply offset in `setTime()`

**Location:** the `setTime(t)` method in the highway API object

**Reason:** applying the offset here means every call site — the audio sync loop,
loop rewind animation, and count-in — all receive the correction automatically.

```javascript
// Before:
        setTime(t) { chartTime = t; currentTime = t + avOffsetSec; },

// After:
        setTime(t) { chartTime = t; currentTime = t + avOffsetSec + songOffset; },
```

---

## Changes to `static/app.js`

### 16. Add `FOLDER` format badge

**Location:** in `formatBadge()` and `formatBadgeInline()` functions, before the
final `return` in each

**Reason:** loose folder songs should display a distinct `FOLDER` badge in the
library, consistent with the `PSARC` and `SLOPPAK` badges on other formats.
Amber colouring distinguishes it from both.

```javascript
// In formatBadge(), before the final return:
    if (fmt === 'loose') {
        return `<span class="fmt-badge absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-900/80 text-amber-200 border border-amber-700">FOLDER</span>`;
    }

// In formatBadgeInline(), before the final return:
    if (fmt === 'loose') {
        return `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-900/60 text-amber-300">FOLDER</span>`;
    }
```

---

## Changes to `static/index.html`

### 17. Add `Folder` option to format filter dropdown

**Location:** in the `lib-format` select element

**Reason:** allows users to filter the library to show only loose folder songs.

```html
<!-- Before: -->
<option value="">All formats</option>
<option value="psarc">PSARC</option>
<option value="sloppak">Sloppak</option>

<!-- After: -->
<option value="">All formats</option>
<option value="psarc">PSARC</option>
<option value="sloppak">Sloppak</option>
<option value="loose">Folder</option>
```

---

## Companion plugin: `plugins/loose_folder/`

The plugin is a self-contained optional addition that provides a **Fix Sync** UI
for correcting audio/chart timing on a per-song basis.

### Why a separate plugin?

Charts in a loose/testing state may have been authored against a different audio
file than the one currently available, resulting in the chart's `<offset>` value
not perfectly matching the actual audio lead-in. The core changes apply the stored
offset automatically, but if that offset is wrong the user needs a way to correct it.

The plugin provides a slider in the player controls bar (visible only for loose
folder songs) that lets the user nudge the offset in real time until notes line up
with the audio, then save the correction. On save, the plugin rewrites the
`<offset>` tag in all arrangement XMLs in the folder that share the same original
offset value, recording a comment history so corrections are reversible.

### Plugin files

```
plugins/loose_folder/
    plugin.json     — id, name, version, routes and script declarations
    routes.py       — API endpoints: sync_status, sync_save, sync_revert
    screen.js       — Fix Sync button and slider UI injected into the player HUD
```

### Plugin API endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/plugins/loose_folder/sync_status` | GET | Returns current and original offset for a song |
| `/api/plugins/loose_folder/sync_save` | POST | Writes corrected offset to all matching XMLs |
| `/api/plugins/loose_folder/sync_revert` | POST | Reverts one step or all the way to original |

### Comment history format

The plugin preserves a full audit trail in each XML file. The original value is
never lost — only the `<offset>` tag value changes, with comments recording every
correction:

```xml
<!-- original_offset: 2.443 -->
<!-- edited_offset: 4.830 -->
<offset>4.830</offset>
```

Subsequent corrections append further `edited_offset` comments. The `sync_revert`
endpoint can step back one correction at a time or revert all the way to the
original in a single operation.

---

## Summary of files

| File | Type | Purpose |
| --- | --- | --- |
| `lib/loosefolder.py` | New file | Core library: detection, metadata, audio/art lookup |
| `server.py` | Modified | Scanner, dispatcher, art serving, highway WS, offset |
| `static/highway.js` | Modified | songOffset variable, capture, and application |
| `static/app.js` | Modified | FOLDER badge in grid and list views |
| `static/index.html` | Modified | Folder option in format filter dropdown |
| `plugins/loose_folder/plugin.json` | New file | Plugin manifest |
| `plugins/loose_folder/routes.py` | New file | Sync correction API |
| `plugins/loose_folder/screen.js` | New file | Fix Sync HUD UI |
