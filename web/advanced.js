'use strict';

(() => {
  if (window.filmAdvancedToolsInstalled) return;
  window.filmAdvancedToolsInstalled = true;

  const toolbar = document.querySelector('.timeline-toolbar');
  if (!toolbar) return;

  const row = (label, control) => {
    const wrap = el('label', undefined, 'film-advanced-field');
    wrap.append(el('span', label), control);
    return wrap;
  };
  const input = (id, type = 'number', value = '') => {
    const node = el('input');
    node.id = id;
    node.type = type;
    if (value !== '') node.value = value;
    return node;
  };
  const section = (title, description) => {
    const node = el('section', undefined, 'film-advanced-section');
    node.append(el('h3', title));
    if (description) node.append(el('p', description, 'film-advanced-help'));
    return node;
  };
  const action = (label, handler, className = '') => {
    const node = el('button', label, className);
    node.type = 'button';
    node.addEventListener('click', async () => {
      node.disabled = true;
      try {
        await handler();
      } catch (error) {
        notice(errorText(error), true);
      } finally {
        node.disabled = false;
        refreshMarkerOverlay();
        updateDialog();
      }
    });
    return node;
  };
  const dialog = el('dialog');
  dialog.id = 'film-advanced-dialog';
  dialog.className = 'film-advanced-dialog';

  const heading = el('div', undefined, 'film-advanced-heading');
  const title = el('div');
  title.append(el('span', 'TIMELINE TOOLS', 'eyebrow'), el('h2', 'More ways to shape your edit'));
  heading.append(title, action('×', () => dialog.close(), 'film-advanced-close'));
  dialog.append(heading, el('p', 'These controls apply revision-checked edits to the current sequence. Undo is available from the timeline.', 'film-advanced-intro'));

  const selectionStatus = el('p', 'Select a timeline clip to use clip tools.', 'film-advanced-selection');
  selectionStatus.id = 'film-advanced-selection';
  dialog.append(selectionStatus);

  const trims = section('Frame adjustments', 'Use positive or negative frame counts. Roll changes a cut; slip changes source frames; slide moves the selected clip between adjacent clips.');
  const delta = input('film-advanced-delta', 'number', '1');
  delta.min = '-10000'; delta.max = '10000'; delta.step = '1';
  trims.append(row('Frames', delta));
  const trimActions = el('div', undefined, 'film-advanced-actions');
  trimActions.append(
    action('Roll cut', async () => {
      const { item } = clip();
      await commit([{ op: 'roll_edit', item_ref: item.item_ref, delta: frameDelta() }], item.producer_id);
      notice('Cut rolled.');
    }),
    action('Slip source', async () => {
      const { item } = clip();
      await commit([{ op: 'slip_item', item_ref: item.item_ref, delta: frameDelta() }], item.producer_id);
      notice('Source range slipped.');
    }),
    action('Slide clip', async () => {
      const { item } = clip();
      await commit([{ op: 'slide_item', item_ref: item.item_ref, delta: frameDelta() }], item.producer_id);
      notice('Clip slid between its neighbors.');
    })
  );
  trims.append(trimActions);
  dialog.append(trims);

  const speedSection = section('Speed and reverse', 'Set a constant playback speed for the selected media clip. Negative speeds reverse playback.');
  const speed = input('film-advanced-speed', 'number', '1');
  speed.min = '-100'; speed.max = '100'; speed.step = '0.05';
  const pitchWrap = el('label', undefined, 'film-advanced-check');
  const pitch = input('film-advanced-pitch', 'checkbox'); pitch.checked = true;
  pitchWrap.append(pitch, el('span', 'Preserve audio pitch'));
  speedSection.append(row('Speed (×)', speed), pitchWrap);
  const speedActions = el('div', undefined, 'film-advanced-actions');
  speedActions.append(
    action('Apply speed', () => applySpeed(Number(speed.value))),
    action('Reverse', () => {
      const next = -Math.abs(Number(speed.value) || 1);
      speed.value = String(next);
      return applySpeed(next);
    })
  );
  speedSection.append(speedActions);
  dialog.append(speedSection);

  const transitionSection = section('Cross dissolve', 'Add an MLT luma dissolve at the cut immediately after the selected clip. The cut must have enough source handles.');
  const transitionDuration = input('film-advanced-transition-duration', 'number', '15');
  transitionDuration.min = '1'; transitionDuration.max = '10000'; transitionDuration.step = '1';
  transitionSection.append(row('Duration (frames)', transitionDuration));
  transitionSection.append(action('Add cross dissolve after selected clip', async () => {
    const { track, item } = clip();
    const next = track.items.find(candidate => candidate.item_index === item.item_index + 1);
    if (!next || next.type !== 'clip' || next.start_frame !== item.start_frame + item.duration_frames) {
      throw new Error('Select a clip with a contiguous clip immediately to its right.');
    }
    await requireService('transition', 'luma');
    await commit([{ op: 'add_transition', item_ref: item.item_ref,
      duration_frames: positiveInteger(transitionDuration.value, 'Transition duration'),
      service: 'luma', audio_crossfade: true, name: 'Cross dissolve' }], item.producer_id);
    notice('Cross dissolve added. Render a preview to review the transition.');
  }, 'film-advanced-primary'));
  dialog.append(transitionSection);

  const titleSection = section('Title generator', 'Create a new video track and place a text overlay at the playhead.');
  const titleText = el('textarea');
  titleText.id = 'film-advanced-title-text';
  titleText.rows = 2;
  titleText.maxLength = 500;
  titleText.placeholder = 'Your title';
  const titleDuration = input('film-advanced-title-duration', 'number', '3');
  titleDuration.min = '0.1'; titleDuration.max = '600'; titleDuration.step = '0.1';
  titleSection.append(row('Text', titleText), row('Duration (seconds)', titleDuration));
  titleSection.append(action('Create title on a new video track', async () => {
    const text = titleText.value.trim();
    if (!text) throw new Error('Enter title text first.');
    const seconds = Number(titleDuration.value);
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 600) throw new Error('Title duration must be between 0 and 600 seconds.');
    const trackName = uniqueTrackName('Titles');
    const duration = Math.max(1, Math.round(seconds * fps()));
    await edit([
      { op: 'add_track', kind: 'video', name: trackName },
      { op: 'add_generator', track: trackName, generator: 'text', text,
        duration_frames: duration, position_frame: Math.max(0, Math.round(playhead)), mode: 'insert' }
    ]);
    notice('Title added to ' + trackName + '.');
  }, 'film-advanced-primary'));
  dialog.append(titleSection);

  const markerSection = section('Sequence markers', 'Add a point marker at the playhead. Markers appear on the timeline ruler and remain editable in the project.');
  const markerText = input('film-advanced-marker-text', 'text', 'Marker');
  markerText.maxLength = 120;
  markerSection.append(row('Label', markerText));
  const markerActions = el('div', undefined, 'film-advanced-actions');
  markerActions.append(
    action('Add marker at playhead', async () => {
      const label = markerText.value.trim();
      if (!label) throw new Error('Enter a marker label.');
      await edit([{ op: 'add_marker', start_frame: Math.max(0, Math.round(playhead)), text: label, color: '#66aaff' }]);
      notice('Marker added at ' + tc(playhead) + '.');
    }),
    action('Remove selected marker', async () => {
      if (!markerSelect.value) throw new Error('Choose a marker to remove.');
      await edit([{ op: 'remove_marker', marker_id: markerSelect.value }]);
      notice('Marker removed.');
    })
  );
  const markerSelect = el('select'); markerSelect.id = 'film-advanced-marker-select';
  markerSection.append(row('Remove marker', markerSelect), markerActions);
  dialog.append(markerSection);

  const captionSection = section('SRT captions', 'Import an .srt file or edit the caption text below. The file is read locally; only parsed caption text is saved to the sequence.');
  const captionLoadRow = el('div', undefined, 'film-advanced-actions');
  const captionExisting = el('select'); captionExisting.id = 'film-advanced-caption-existing';
  const captionLoad = action('Load selected track', () => loadCaptionTrack());
  captionLoadRow.append(captionExisting, captionLoad);
  const captionName = input('film-advanced-caption-name', 'text', 'English');
  captionName.maxLength = 80;
  const captionLanguage = input('film-advanced-caption-language', 'text', 'eng');
  captionLanguage.maxLength = 16;
  const captionBurn = input('film-advanced-caption-burn', 'checkbox');
  captionBurn.checked = true;
  const burnWrap = el('label', undefined, 'film-advanced-check');
  burnWrap.append(captionBurn, el('span', 'Burn captions into rendered video'));
  const captionFile = input('film-advanced-caption-file', 'file');
  captionFile.accept = '.srt,application/x-subrip,text/plain';
  captionFile.id = 'film-advanced-caption-file';
  const captionText = el('textarea');
  captionText.id = 'film-advanced-caption-text';
  captionText.rows = 8;
  captionText.spellcheck = false;
  captionText.placeholder = '1\n00:00:00,000 --> 00:00:02,000\nCaption text';
  captionSection.append(captionLoadRow, row('Track name', captionName), row('Language code', captionLanguage), burnWrap,
    row('Import SRT', captionFile), row('Caption text', captionText));
  const captionActions = el('div', undefined, 'film-advanced-actions');
  captionActions.append(
    action('Save captions', async () => {
      const name = captionName.value.trim();
      const language = captionLanguage.value.trim();
      if (!name || !language) throw new Error('Caption track name and language code are required.');
      const items = parseSrt(captionText.value);
      if (!items.length) throw new Error('Add at least one SRT caption cue.');
      await edit([{ op: 'set_subtitle_track', name, language, items, burn_in: captionBurn.checked }]);
      notice(items.length + ' caption cue(s) saved to ' + name + '.');
    }, 'film-advanced-primary'),
    action('Remove caption track', async () => {
      const name = captionExisting.value;
      if (!name) throw new Error('Choose a caption track to remove.');
      await edit([{ op: 'remove_subtitle_track', name }]);
      captionText.value = '';
      captionName.value = 'English';
      notice('Caption track removed.');
    })
  );
  captionSection.append(captionActions);
  dialog.append(captionSection);

  const footer = el('div', undefined, 'film-advanced-footer');
  footer.append(el('span', 'All changes can be undone from the timeline.'));
  footer.append(action('Done', () => dialog.close(), 'film-advanced-primary'));
  dialog.append(footer);
  document.body.append(dialog);

  const open = el('button', 'Edit tools…');
  open.id = 'film-advanced-open';
  open.type = 'button';
  open.title = 'Frame adjustments, speed, titles, markers, and captions';
  const anchor = $('add-track') || $('zoom-fit');
  anchor?.insertAdjacentElement('afterend', open);
  open.addEventListener('click', () => {
    updateDialog();
    if (!dialog.open) dialog.showModal();
  });

  delta.addEventListener('input', updateDialog);
  speed.addEventListener('input', updateDialog);
  transitionDuration.addEventListener('input', updateDialog);
  captionExisting.addEventListener('change', updateDialog);
  captionFile.addEventListener('change', async () => {
    const file = captionFile.files?.[0];
    if (!file) return;
    try {
      captionText.value = await file.text();
      if (!captionName.value || captionName.value === 'English') captionName.value = file.name.replace(/\.srt$/i, '').slice(0, 80) || 'Captions';
      if (!captionLanguage.value) captionLanguage.value = 'eng';
      notice('Loaded SRT text from ' + file.name + '.');
    } catch (error) {
      notice('Could not read the SRT file: ' + errorText(error), true);
    }
  });

  function frameDelta() {
    const amount = Number(delta.value);
    if (!Number.isSafeInteger(amount) || amount === 0 || Math.abs(amount) > 10000) throw new Error('Frame adjustment must be a nonzero whole number from −10,000 to 10,000.');
    return amount;
  }
  function positiveInteger(raw, label) {
    const n = Number(raw);
    if (!Number.isSafeInteger(n) || n <= 0 || n > 10000) throw new Error(label + ' must be a whole number from 1 to 10,000.');
    return n;
  }
  function selected() {
    try { return clip(); } catch { return null; }
  }
  function adjacent(track, item, offset) {
    return track.items.find(candidate => candidate.item_index === item.item_index + offset) || null;
  }
  function contiguous(a, b) {
    return !!a && !!b && a.type === 'clip' && b.type === 'clip' && a.start_frame + a.duration_frames === b.start_frame;
  }
  function uniqueTrackName(base) {
    const names = new Set((timeline?.tracks || []).map(track => track.name));
    if (!names.has(base)) return base;
    let n = 2;
    while (names.has(base + ' ' + n)) n += 1;
    return base + ' ' + n;
  }
  async function commit(operations, producerId) {
    const next = await edit(operations);
    if (producerId) {
      for (const track of next.tracks || []) {
        const found = track.items.find(item => item.type === 'clip' && item.producer_id === producerId);
        if (found) {
          selectedClip = found.item_ref;
          selectedTrack = track.track_id;
          drawTimeline();
          break;
        }
      }
    }
    updateDialog();
    return next;
  }
  async function applySpeed(value) {
    if (!Number.isFinite(value) || value === 0 || Math.abs(value) < .05 || Math.abs(value) > 100) throw new Error('Speed must be between 0.05× and 100×; use a negative value to reverse.');
    const { item } = clip();
    await commit([{ op: 'set_clip_speed', item_ref: item.item_ref, speed: value, pitch_compensation: pitch.checked }], item.producer_id);
    notice('Playback speed set to ' + value + '×. Render a preview to review.');
  }
  let services = null;
  async function requireService(kind, name) {
    if (!services) services = await call('list_mlt_services', { kind });
    if (services.kind !== kind || !Array.isArray(services.services) || !services.services.includes(name)) {
      throw new Error('The installed MLT engine does not provide the ' + name + ' ' + kind + ' service.');
    }
  }
  function updateDialog() {
    if (!timeline) return;
    const current = selected();
    if (current) {
      const { track, item } = current;
      selectionStatus.textContent = item.name + ' · ' + track.name + ' · ' + tc(item.start_frame) + '–' + tc(item.end_frame + 1);
      const prev = adjacent(track, item, -1), next = adjacent(track, item, 1);
      const buttons = trimActions.querySelectorAll('button');
      buttons[0].disabled = !contiguous(item, next);
      buttons[1].disabled = item.type !== 'clip';
      buttons[2].disabled = !contiguous(prev, item) || !contiguous(item, next);
      transitionSection.querySelector('button').disabled = !contiguous(item, next);
      speedActions.querySelectorAll('button').forEach(button => button.disabled = item.type !== 'clip' || !item.asset_id);
    } else {
      selectionStatus.textContent = 'Select a timeline clip to use clip tools.';
      trimActions.querySelectorAll('button').forEach(button => button.disabled = true);
      transitionSection.querySelector('button').disabled = true;
      speedActions.querySelectorAll('button').forEach(button => button.disabled = true);
    }
    const oldMarker = markerSelect.value;
    markerSelect.replaceChildren();
    const markers = timeline.markers || [];
    for (const marker of markers) {
      const option = el('option', (marker.text || 'Marker') + ' · ' + tc(marker.start_frame || 0));
      option.value = marker.marker_id;
      markerSelect.append(option);
    }
    if (markers.some(marker => marker.marker_id === oldMarker)) markerSelect.value = oldMarker;
    else if (markers.length) markerSelect.selectedIndex = 0;
    else markerSelect.append(el('option', 'No markers'));
    const oldCaption = captionExisting.value;
    captionExisting.replaceChildren(el('option', 'Choose a caption track…'));
    captionExisting.firstChild.value = '';
    for (const subtitle of timeline.subtitles || []) {
      const option = el('option', subtitle.name || 'Captions');
      option.value = subtitle.name || '';
      captionExisting.append(option);
    }
    if ([...captionExisting.options].some(option => option.value === oldCaption)) captionExisting.value = oldCaption;
    refreshMarkerOverlay();
  }
  function loadCaptionTrack() {
    const subtitle = (timeline?.subtitles || []).find(item => item.name === captionExisting.value);
    if (!subtitle) throw new Error('Choose a caption track first.');
    captionName.value = subtitle.name || 'Captions';
    captionLanguage.value = subtitle.language || 'eng';
    captionText.value = subtitle.srt || '';
    notice('Loaded ' + captionName.value + ' captions for editing.');
  }
  function timestampMs(raw) {
    const match = String(raw).match(/^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$/);
    if (!match) throw new Error('Invalid SRT timecode: ' + raw);
    const [, hours, minutes, seconds, millis] = match.map((part, index) => index ? Number(part) : part);
    if (minutes > 59 || seconds > 59) throw new Error('Invalid SRT timecode: ' + raw);
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis;
  }
  function parseSrt(source) {
    const normalized = String(source).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim();
    if (!normalized) return [];
    const items = [];
    for (const block of normalized.split(/\n{2,}/)) {
      const lines = block.split('\n');
      const timingIndex = lines.findIndex(line => line.includes('-->'));
      if (timingIndex < 0) throw new Error('An SRT cue is missing its timecode.');
      const match = lines[timingIndex].match(/^\s*(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2,}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?\s*$/);
      if (!match) throw new Error('Invalid SRT timing line: ' + lines[timingIndex]);
      const text = lines.slice(timingIndex + 1).join('\n').trim();
      const start_ms = timestampMs(match[1]);
      const end_ms = timestampMs(match[2]);
      if (!text || end_ms <= start_ms) throw new Error('Each SRT cue needs text and a positive duration.');
      items.push({ start_ms, end_ms, text });
    }
    items.sort((a, b) => a.start_ms - b.start_ms);
    for (let index = 1; index < items.length; index += 1) {
      if (items[index].start_ms < items[index - 1].end_ms) throw new Error('SRT cues may not overlap.');
    }
    return items;
  }
  function refreshMarkerOverlay() {
    const ruler = $('ruler');
    if (!ruler) return;
    ruler.querySelector('.film-advanced-marker-overlay')?.remove();
    if (!timeline || !Array.isArray(timeline.markers) || !timeline.markers.length) return;
    const overlay = el('div', undefined, 'film-advanced-marker-overlay');
    overlay.id = 'film-advanced-marker-overlay';
    overlay.setAttribute('aria-label', 'Sequence markers');
    for (const marker of timeline.markers) {
      if (!Number.isFinite(marker.start_frame)) continue;
      const pin = el('span', undefined, 'film-advanced-marker');
      pin.append(el('span', marker.text || 'Marker', 'film-advanced-marker-label'));
      pin.style.left = (marker.start_frame / fps() * zoom) + 'px';
      pin.style.setProperty('--marker-color', marker.color || '#66aaff');
      pin.title = (marker.text || 'Marker') + ' · ' + tc(marker.start_frame);
      overlay.append(pin);
    }
    ruler.append(overlay);
  }

  if (typeof drawTimeline === 'function') {
    const draw = drawTimeline;
    drawTimeline = function (...args) {
      const result = draw.apply(this, args);
      refreshMarkerOverlay();
      return result;
    };
  }
  updateDialog();
})();
