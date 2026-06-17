// Loose Folder Support — Fix Sync UI
// Injects a "Fix Sync" button into the player controls bar when the
// currently loaded song is from a loose folder. Allows the user to
// adjust the audio/chart offset with a live-preview slider, then save
// the correction back to the XML files in the folder.

(function () {
'use strict';

const PLUGIN_ID      = 'loose_folder';
const PREVIEW_SEC_DEFAULT = 5;   // seconds of playback per preview
const OFFSET_MIN     = -15.0;
const OFFSET_MAX     =  15.0;
const OFFSET_STEP    =  0.05;

// ── State ────────────────────────────────────────────────────────────
let _currentFilename = '';
let _baseOffset      = 0.0;   // offset as loaded from the XML
let _liveOffset      = 0.0;   // current slider value (applied live)
let _previewSec      = PREVIEW_SEC_DEFAULT;
let _previewTimer    = null;
let _syncPanelEl     = null;
let _fixBtnEl        = null;
let _panelVisible    = false;
let _hasCorrection   = false;

// ── Helpers ──────────────────────────────────────────────────────────

function _fmt(v) {
    const sign = v >= 0 ? '+' : '';
    return sign + v.toFixed(2) + 's';
}

function _applyLiveOffset(delta) {
    // delta is the adjustment relative to the base offset.
    // highway.setTime already adds songOffset internally, so we
    // surface the correction via a temporary override by patching
    // the song's offset field that highway reads each frame.
    // Simplest approach: dispatch a custom event that app.js /
    // highway can listen to. If that surface isn't available we
    // fall back to directly calling highway.setTime with a biased
    // value on the next tick via a rAF loop (see _startPreview).
    const event = new CustomEvent('loosefolder:offsetchange', { detail: { delta } });
    window.dispatchEvent(event);
}

async function _fetchStatus(filename) {
    try {
        const r = await fetch(`/api/plugins/${PLUGIN_ID}/sync_status?filename=${encodeURIComponent(filename)}`);
        if (!r.ok) return null;
        return await r.json();
    } catch (_) { return null; }
}

async function _saveSync(filename, offset) {
    try {
        const r = await fetch(`/api/plugins/${PLUGIN_ID}/sync_save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, offset }),
        });
        return r.ok ? await r.json() : null;
    } catch (_) { return null; }
}

async function _revertSync(filename, mode) {
    try {
        const r = await fetch(`/api/plugins/${PLUGIN_ID}/sync_revert`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, mode }),
        });
        return r.ok ? await r.json() : null;
    } catch (_) { return null; }
}

// ── Preview playback ─────────────────────────────────────────────────

function _stopPreview() {
    if (_previewTimer) {
        clearTimeout(_previewTimer);
        _previewTimer = null;
    }
}

function _startPreview() {
    _stopPreview();
    const audio = document.getElementById('audio');
    if (!audio) return;

    // Seek to current position and play for _previewSec seconds
    const startTime = audio.currentTime;
    audio.play().catch(() => {});

    _previewTimer = setTimeout(() => {
        audio.pause();
        _previewTimer = null;
    }, _previewSec * 1000);
}

// ── UI construction ──────────────────────────────────────────────────

function _removeSyncPanel() {
    if (_syncPanelEl) {
        _syncPanelEl.remove();
        _syncPanelEl = null;
    }
    _panelVisible = false;
}

function _buildSyncPanel(status) {
    _removeSyncPanel();

    _baseOffset  = status.current_offset;
    _liveOffset  = 0.0;
    _hasCorrection = status.has_correction;

    const panel = document.createElement('div');
    panel.id = 'loosefolder-sync-panel';
    panel.style.cssText = [
        'display:flex', 'align-items:center', 'gap:10px', 'flex-wrap:wrap',
        'padding:6px 12px', 'background:rgba(10,10,25,0.96)',
        'border-top:1px solid #2a2a3a', 'font-size:11px', 'color:#aaa',
        'position:relative', 'z-index:50',
    ].join(';');

    // Label
    const label = document.createElement('span');
    label.style.cssText = 'color:#6b7280;white-space:nowrap;';
    label.textContent = 'Sync Offset';
    panel.appendChild(label);

    // Slider
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min  = String(OFFSET_MIN);
    slider.max  = String(OFFSET_MAX);
    slider.step = String(OFFSET_STEP);
    slider.value = '0';
    slider.style.cssText = 'width:180px;accent-color:#6366f1;';
    slider.title = 'Drag to adjust chart/audio sync. 0 = no change from current.';

    const valLabel = document.createElement('span');
    valLabel.style.cssText = 'min-width:52px;color:#c4b5fd;font-variant-numeric:tabular-nums;';
    valLabel.textContent = _fmt(0);

    slider.addEventListener('input', () => {
        _liveOffset = parseFloat(slider.value);
        valLabel.textContent = _fmt(_liveOffset);
        _applyLiveOffset(_liveOffset);
    });

    panel.appendChild(slider);
    panel.appendChild(valLabel);

    // Preview duration
    const prevLabel = document.createElement('span');
    prevLabel.style.cssText = 'color:#6b7280;margin-left:6px;';
    prevLabel.textContent = 'Preview:';
    panel.appendChild(prevLabel);

    const prevInput = document.createElement('input');
    prevInput.type = 'number';
    prevInput.min = '1';
    prevInput.max = '30';
    prevInput.step = '1';
    prevInput.value = String(_previewSec);
    prevInput.style.cssText = [
        'width:44px', 'background:#1a1a2e', 'border:1px solid #333',
        'border-radius:6px', 'padding:2px 5px', 'color:#ccc', 'text-align:center',
    ].join(';');
    prevInput.addEventListener('change', () => {
        const v = parseInt(prevInput.value);
        if (v >= 1 && v <= 30) _previewSec = v;
    });
    panel.appendChild(prevInput);

    const prevSLabel = document.createElement('span');
    prevSLabel.style.cssText = 'color:#6b7280;';
    prevSLabel.textContent = 's';
    panel.appendChild(prevSLabel);

    // Preview button
    const previewBtn = document.createElement('button');
    previewBtn.type = 'button';
    previewBtn.textContent = '▶ Preview';
    previewBtn.style.cssText = _btnStyle('#1e3a5f', '#2563eb');
    previewBtn.addEventListener('click', _startPreview);
    panel.appendChild(previewBtn);

    // Save button
    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.textContent = '💾 Save';
    saveBtn.style.cssText = _btnStyle('#14532d', '#16a34a');
    saveBtn.addEventListener('click', async () => {
        const finalOffset = _baseOffset + _liveOffset;
        saveBtn.textContent = 'Saving…';
        saveBtn.disabled = true;
        const result = await _saveSync(_currentFilename, finalOffset);
        if (result && result.updated && result.updated.length > 0) {
            _baseOffset  = finalOffset;
            _liveOffset  = 0.0;
            slider.value = '0';
            valLabel.textContent = _fmt(0);
            saveBtn.textContent = '✓ Saved';
            _hasCorrection = true;
            revertStepBtn.style.display = '';
            revertAllBtn.style.display  = '';
            setTimeout(() => {
                saveBtn.textContent = '💾 Save';
                saveBtn.disabled = false;
            }, 2000);
        } else {
            saveBtn.textContent = '✕ Failed';
            setTimeout(() => {
                saveBtn.textContent = '💾 Save';
                saveBtn.disabled = false;
            }, 2000);
        }
    });
    panel.appendChild(saveBtn);

    // Revert one step
    const revertStepBtn = document.createElement('button');
    revertStepBtn.type = 'button';
    revertStepBtn.textContent = '↩ Undo';
    revertStepBtn.title = 'Revert to previous saved offset';
    revertStepBtn.style.cssText = _btnStyle('#3b1f1f', '#b91c1c');
    revertStepBtn.style.display = _hasCorrection ? '' : 'none';
    revertStepBtn.addEventListener('click', async () => {
        revertStepBtn.textContent = '…';
        revertStepBtn.disabled = true;
        const result = await _revertSync(_currentFilename, 'step');
        if (result) {
            // Re-fetch status to get updated offset
            const status = await _fetchStatus(_currentFilename);
            if (status) {
                _baseOffset    = status.current_offset;
                _hasCorrection = status.has_correction;
                _liveOffset    = 0.0;
                slider.value   = '0';
                valLabel.textContent = _fmt(0);
                revertStepBtn.style.display = _hasCorrection ? '' : 'none';
                revertAllBtn.style.display  = _hasCorrection ? '' : 'none';
            }
        }
        revertStepBtn.textContent = '↩ Undo';
        revertStepBtn.disabled = false;
    });
    panel.appendChild(revertStepBtn);

    // Revert all
    const revertAllBtn = document.createElement('button');
    revertAllBtn.type = 'button';
    revertAllBtn.textContent = '↩↩ Revert All';
    revertAllBtn.title = 'Revert to original offset from when the chart was first corrected';
    revertAllBtn.style.cssText = _btnStyle('#3b1f1f', '#b91c1c');
    revertAllBtn.style.display = _hasCorrection ? '' : 'none';
    revertAllBtn.addEventListener('click', async () => {
        if (!confirm('Revert all sync corrections to the original offset?')) return;
        revertAllBtn.textContent = '…';
        revertAllBtn.disabled = true;
        const result = await _revertSync(_currentFilename, 'all');
        if (result) {
            const status = await _fetchStatus(_currentFilename);
            if (status) {
                _baseOffset    = status.current_offset;
                _hasCorrection = status.has_correction;
                _liveOffset    = 0.0;
                slider.value   = '0';
                valLabel.textContent = _fmt(0);
                revertStepBtn.style.display = 'none';
                revertAllBtn.style.display  = 'none';
            }
        }
        revertAllBtn.textContent = '↩↩ Revert All';
        revertAllBtn.disabled = false;
    });
    panel.appendChild(revertAllBtn);

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.textContent = '✕';
    closeBtn.title = 'Close sync panel';
    closeBtn.style.cssText = _btnStyle('#1f1f2e', '#374151') + ';margin-left:auto;';
    closeBtn.addEventListener('click', () => {
        _stopPreview();
        _liveOffset = 0.0;
        _applyLiveOffset(0.0);
        _removeSyncPanel();
        _panelVisible = false;
        if (_fixBtnEl) _fixBtnEl.textContent = '⏱ Fix Sync';
    });
    panel.appendChild(closeBtn);

    return panel;
}

function _btnStyle(bg, hover) {
    return [
        `background:${bg}`, 'border:none', 'border-radius:6px',
        'padding:3px 8px', 'color:#ccc', 'cursor:pointer',
        'font-size:11px', 'white-space:nowrap', 'transition:background 0.15s',
    ].join(';');
}

function _injectFixBtn() {
    _removeFixBtn();

    const controls = document.getElementById('player-controls');
    if (!controls) return;

    const btn = document.createElement('button');
    btn.id   = 'loosefolder-fix-sync-btn';
    btn.type = 'button';
    btn.textContent = '⏱ Fix Sync';
    btn.style.cssText = [
        'padding:6px 10px', 'background:#1e1b4b',
        'border:1px solid #4338ca', 'border-radius:8px',
        'font-size:12px', 'color:#a5b4fc', 'cursor:pointer',
        'white-space:nowrap', 'transition:background 0.15s',
    ].join(';');
    btn.title = 'Adjust chart/audio sync for this song and save the correction';

    btn.addEventListener('click', async () => {
        if (_panelVisible) {
            _stopPreview();
            _liveOffset = 0.0;
            _applyLiveOffset(0.0);
            _removeSyncPanel();
            _panelVisible = false;
            btn.textContent = '⏱ Fix Sync';
            return;
        }

        btn.textContent = '…';
        btn.disabled = true;

        const status = await _fetchStatus(_currentFilename);
        if (!status) {
            btn.textContent = '⏱ Fix Sync';
            btn.disabled = false;
            return;
        }

        const panel = _buildSyncPanel(status);

        // Insert the panel above player-controls
        const player = document.getElementById('player');
        if (player) {
            player.insertBefore(panel, controls);
        }
        _syncPanelEl  = panel;
        _panelVisible = true;

        btn.textContent = '⏱ Sync ✓';
        btn.disabled = false;
    });

    // Insert before the close button (last button in controls)
    const closeBtn = controls.querySelector('button:last-child');
    if (closeBtn) {
        controls.insertBefore(btn, closeBtn);
    } else {
        controls.appendChild(btn);
    }

    _fixBtnEl = btn;
}

function _removeFixBtn() {
    _stopPreview();
    _removeSyncPanel();
    if (_fixBtnEl) {
        _fixBtnEl.remove();
        _fixBtnEl = null;
    }
    _panelVisible = false;
}

// ── Live offset application ──────────────────────────────────────────
// Listen for the custom event we dispatch from _applyLiveOffset and
// wire it into highway's setTime on the next animation frame.

let _offsetOverride = 0.0;
let _rafActive = false;

window.addEventListener('loosefolder:offsetchange', (e) => {
    _offsetOverride = e.detail.delta;
});

// Patch highway.setTime to apply the override while the panel is open.
// We wrap once at load time to avoid repeated wrapping.
(function _patchHighway() {
    const MAX_WAIT = 5000;
    const start = Date.now();
    function tryPatch() {
        if (window.highway && typeof window.highway.setTime === 'function') {
            const _orig = window.highway.setTime.bind(window.highway);
            window.highway.setTime = function (t) {
                _orig(t + (_panelVisible ? _offsetOverride : 0));
            };
        } else if (Date.now() - start < MAX_WAIT) {
            setTimeout(tryPatch, 100);
        }
    }
    tryPatch();
})();

// ── Song change detection ────────────────────────────────────────────

function _onSongReady() {
    const info = window.highway && window.highway.getSongInfo
        ? window.highway.getSongInfo()
        : null;

    const format  = info && info.format ? info.format : '';
    const isLoose = format === 'loose';

    // Filename comes from the slopsmith currentSong context
    const songData  = window.slopsmith && window.slopsmith.currentSong
        ? window.slopsmith.currentSong
        : null;
    const filename  = songData && songData.filename ? songData.filename : '';

    // Always clean up first
    _removeFixBtn();
    _currentFilename = '';

    if (isLoose && filename) {
        _currentFilename = filename;
        _injectFixBtn();
    }
}

// Subscribe to song:ready via the slopsmith event bus
if (window.slopsmith) {
    window.slopsmith.on('song:ready', _onSongReady);
} else {
    // Fallback: poll until slopsmith is available
    const interval = setInterval(() => {
        if (window.slopsmith) {
            window.slopsmith.on('song:ready', _onSongReady);
            clearInterval(interval);
        }
    }, 200);
}

// Also handle screen changes — clean up when leaving the player
document.addEventListener('slopsmith:screenchange', (e) => {
    if (e.detail && e.detail.screen !== 'player') {
        _removeFixBtn();
    }
});

})();
