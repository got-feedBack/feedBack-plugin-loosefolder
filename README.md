# Slopsmith Plugin: Loose Folder Support

A plugin that adds a **Fix Sync** UI for loose-folder songs — raw assets (`audio.wem` + arrangement XMLs) that haven't been packaged into a Sloppak yet.

> **Requires core changes to slopsmith.** The plugin alone is not functional. See [`docs/core-integration/CORE_CHANGES.md`](docs/core-integration/CORE_CHANGES.md) — these edits land via a companion PR to the slopsmith core repo and add scanner discovery, audio loading, art serving, and the `format: "loose"` flag the plugin keys off.

## Features

- **Fix Sync slider** in the player controls (only visible for loose-folder songs) — nudge the chart/audio offset in real time.
- **Live preview** — apply the offset to the highway immediately, with a short timed playback button to A/B against.
- **Save** — rewrites `<offset>` in every matching arrangement XML in the folder, while preserving the original value as a comment for audit/revert.
- **Undo / Revert All** — step back one correction at a time, or revert all the way to the original offset.

## Loose folder layout

```
song_dir/
├── audio.wem                    (required — also accepts song.wem or any non-preview *.wem)
├── lead.xml                     (at least one arrangement XML required)
├── rhythm.xml
├── bass.xml
├── manifest.json                (optional)
└── album_art.jpg                (optional — also cover.jpg, album.png, etc.)
```

`manifest.json` (all fields optional, XML metadata fills the gaps):

```json
{
  "title":          "Song Name",
  "artist":         "Artist Name",
  "album":          "Album Name",
  "year":           "2024",
  "tuning_offsets": [0, 0, 0, 0, 0, 0]
}
```

## Installation

```bash
cd /path/to/slopsmith/plugins
git clone https://github.com/got-feedback/feedBack-plugin-loosefolder.git loose_folder
docker compose restart
```

The plugin directory name **must** be `loose_folder` to match the `id` in `plugin.json`.

## How It Works

1. The slopsmith scanner (with the core changes applied) picks up loose-folder songs from `DLC_DIR` and tags them `format: "loose"`.
2. When you load a loose song, the plugin notices the format flag via `song:ready` and injects a "⏱ Fix Sync" button next to the player controls.
3. Click it to reveal a slider. Drag to bias the chart against the audio in real time; "▶ Preview" plays a short clip at the current setting.
4. Click "💾 Save" to write the new offset back to every arrangement XML in the folder. The plugin seeds a `<!-- original_offset: … -->` comment on first save so corrections are always reversible.

### Sync comment history

The plugin never destroys the original `<offset>` value — it preserves a full audit trail in each XML:

```xml
<!-- original_offset: 2.443 -->
<!-- edited_offset: 4.830 -->
<offset>4.830</offset>
```

Subsequent corrections append further `edited_offset` comments. "↩ Undo" steps back one entry; "↩↩ Revert All" jumps to the original.

## API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/plugins/loose_folder/sync_status` | GET | Returns current and original offset for a song |
| `/api/plugins/loose_folder/sync_save` | POST | Writes corrected offset to all matching XMLs |
| `/api/plugins/loose_folder/sync_revert` | POST | Reverts one step (`mode=step`) or all the way (`mode=all`) |

## Notes

- Folder paths are validated with `Path.resolve().relative_to(DLC_DIR)` — path traversal is blocked.
- The plugin only operates on the first non-vocals, non-showlight XML in the folder; corrections are then mirrored to every other arrangement XML that shares the same effective original offset.
- This plugin is intended for *authoring/testing* loose songs. Once you're happy with timing, package the folder into a Sloppak for distribution.

## License

Same as the parent slopsmith project.
