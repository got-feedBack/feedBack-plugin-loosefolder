"""Loose Folder Support — API routes for sync correction."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse


def setup(app, context):
    get_dlc_dir = context["get_dlc_dir"]
    log = context["log"]

    def _song_dir(filename: str) -> Path | None:
        """Resolve a loose folder path from a relative filename."""
        dlc = get_dlc_dir()
        if not dlc:
            return None
        p = (dlc / filename).resolve()
        try:
            p.relative_to(dlc.resolve())
        except ValueError:
            return None
        return p if p.is_dir() else None

    def _read_xml_offset(xml_path: Path) -> str | None:
        """Return the current <offset> value from an XML file as a string."""
        try:
            tree = ET.parse(str(xml_path))
            el = tree.getroot().find("offset")
            return el.text.strip() if el is not None and el.text else None
        except Exception:
            log.exception("Failed to read offset from %s", xml_path)
            return None

    def _get_original_offset(xml_path: Path) -> str | None:
        """Return the original offset value from the <!-- original_offset: X --> comment."""
        try:
            text = xml_path.read_text(encoding="utf-8")
        except Exception:
            log.exception("Failed to read %s", xml_path)
            return None

        m = re.search(r'<!--\s*original_offset:\s*([\d\.\-]+)\s*-->', text)
        if m:
            return m.group(1)
        return None

    def _apply_sync_correction(xml_path: Path, new_offset: float) -> bool:
        """Rewrite <offset> and append a comment recording the change."""
        try:
            text = xml_path.read_text(encoding="utf-8")
        except Exception:
            log.exception("Failed to read %s", xml_path)
            return False

        current_offset = _read_xml_offset(xml_path)
        if current_offset is None:
            return False

        new_offset_str = f"{new_offset:.6f}".rstrip('0').rstrip('.')

        # Seed an original_offset comment the first time we edit this file
        if not re.search(r'<!--\s*original_offset:', text):
            text = re.sub(
                r'(\s*<offset>)',
                f'\n  <!-- original_offset: {current_offset} -->\n\\1',
                text,
                count=1,
            )

        # Capture the comment block (group 1) and the whitespace (group 2) before
        # <offset> separately, then rebuild with a fresh edited_offset comment.
        # Using explicit groups avoids the str.rstrip('<offset>') character-set bug.
        text = re.sub(
            r'((?:<!--\s*(?:original|edited)_offset:[^-]*-->\s*\n)*)(\s*)<offset>',
            lambda m: m.group(1) + m.group(2)
                      + f'<!-- edited_offset: {new_offset_str} -->\n  <offset>',
            text,
            count=1,
        )

        text = re.sub(
            r'(<offset>)[^<]*(</offset>)',
            f'\\g<1>{new_offset_str}\\g<2>',
            text,
            count=1,
        )

        try:
            xml_path.write_text(text, encoding="utf-8")
            return True
        except Exception:
            log.exception("Failed to write sync correction to %s", xml_path)
            return False

    def _revert_to_original(xml_path: Path) -> bool:
        """Revert <offset> to the original_offset comment value."""
        original = _get_original_offset(xml_path)
        if original is None:
            return False
        try:
            text = xml_path.read_text(encoding="utf-8")
        except Exception:
            log.exception("Failed to read %s", xml_path)
            return False

        text = re.sub(
            r'(<offset>)[^<]*(</offset>)',
            f'\\g<1>{original}\\g<2>',
            text,
            count=1,
        )
        try:
            xml_path.write_text(text, encoding="utf-8")
            return True
        except Exception:
            log.exception("Failed to write revert to %s", xml_path)
            return False

    def _revert_one_step(xml_path: Path) -> bool:
        """Revert <offset> to the previous edited_offset value, or original."""
        try:
            text = xml_path.read_text(encoding="utf-8")
        except Exception:
            log.exception("Failed to read %s", xml_path)
            return False

        edited = re.findall(r'<!--\s*edited_offset:\s*([\d\.\-]+)\s*-->', text)
        original = re.search(r'<!--\s*original_offset:\s*([\d\.\-]+)\s*-->', text)

        if len(edited) >= 2:
            target = edited[-2]
        elif len(edited) == 1 and original:
            target = original.group(1)
        elif original:
            target = original.group(1)
        else:
            return False

        text = re.sub(
            r'(<offset>)[^<]*(</offset>)',
            f'\\g<1>{target}\\g<2>',
            text,
            count=1,
        )
        if edited:
            last = edited[-1]
            text = re.sub(
                rf'\s*<!--\s*edited_offset:\s*{re.escape(last)}\s*-->',
                '',
                text,
                count=1,
            )
        try:
            xml_path.write_text(text, encoding="utf-8")
            return True
        except Exception:
            log.exception("Failed to write step-revert to %s", xml_path)
            return False

    @app.get("/api/plugins/loose_folder/sync_status")
    def sync_status(filename: str):
        """Return the current and original offset for a loose folder song."""
        song_dir = _song_dir(filename)
        if not song_dir:
            return JSONResponse({"error": "not found"}, 404)

        xmls = sorted(song_dir.glob("*.xml"))
        if not xmls:
            return JSONResponse({"error": "no xml files"}, 404)

        target_xml = None
        for xml in xmls:
            s = xml.stem.lower()
            if "vocal" in s or "showlight" in s:
                continue
            target_xml = xml
            break

        if not target_xml:
            return JSONResponse({"error": "no arrangement xml"}, 404)

        current = _read_xml_offset(target_xml)
        original = _get_original_offset(target_xml)
        has_correction = original is not None

        return {
            "current_offset": float(current) if current else 0.0,
            "original_offset": float(original) if original else (float(current) if current else 0.0),
            "has_correction": has_correction,
        }

    @app.post("/api/plugins/loose_folder/sync_save")
    async def sync_save(request: Request):
        """Apply a sync correction to all XMLs sharing the same original offset."""
        body = await request.json()
        filename = body.get("filename", "")
        new_offset = body.get("offset")

        if not filename or new_offset is None:
            return JSONResponse({"error": "missing fields"}, 400)

        song_dir = _song_dir(filename)
        if not song_dir:
            return JSONResponse({"error": "not found"}, 404)

        try:
            new_offset = float(new_offset)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid offset"}, 400)

        xmls = [x for x in sorted(song_dir.glob("*.xml"))
                if "vocal" not in x.stem.lower() and "showlight" not in x.stem.lower()]
        if not xmls:
            return JSONResponse({"error": "no arrangement xml files"}, 404)

        first_current = _read_xml_offset(xmls[0])
        first_original = _get_original_offset(xmls[0])
        match_value = first_original or first_current

        updated = []
        skipped = []
        for xml in xmls:
            current = _read_xml_offset(xml)
            original = _get_original_offset(xml)
            effective_original = original or current
            if effective_original == match_value:
                if _apply_sync_correction(xml, new_offset):
                    updated.append(xml.name)
                else:
                    skipped.append(xml.name)
            else:
                skipped.append(xml.name)

        return {"updated": updated, "skipped": skipped, "new_offset": new_offset}

    @app.post("/api/plugins/loose_folder/sync_revert")
    async def sync_revert(request: Request):
        """Revert sync correction — 'step' reverts one edit, 'all' reverts to original."""
        body = await request.json()
        filename = body.get("filename", "")
        mode = body.get("mode", "step")

        if not filename:
            return JSONResponse({"error": "missing filename"}, 400)

        song_dir = _song_dir(filename)
        if not song_dir:
            return JSONResponse({"error": "not found"}, 404)

        xmls = [x for x in sorted(song_dir.glob("*.xml"))
                if "vocal" not in x.stem.lower() and "showlight" not in x.stem.lower()]

        updated = []
        for xml in xmls:
            if mode == "all":
                if _revert_to_original(xml):
                    updated.append(xml.name)
            else:
                if _revert_one_step(xml):
                    updated.append(xml.name)

        return {"reverted": updated, "mode": mode}
